
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Optional, Union, List
from xml.etree.ElementTree import Element
from lxml import etree
from PIL import Image, UnidentifiedImageError
from docling_core.types.doc import DocItemLabel, DoclingDocument, DocumentOrigin, GroupLabel, ImageRef, TableCell, TableData, NodeItem
from docling.backend.abstract_backend import DeclarativeDocumentBackend
from docling.datamodel.base_models import InputFormat
from docling.datamodel.document import InputDocument
from docling_core.types.doc import ImageRefMode
import re
import logging
from collections import defaultdict


class HwpxDocumentBackend(DeclarativeDocumentBackend):
    def __init__(self, in_doc: InputDocument, path_or_stream: Union[Path, BytesIO]) -> None:
        """Initialize the HWPX backend by loading the .hwpx file (zip archive)."""
        super().__init__(in_doc, path_or_stream)
        self.zip = None
        self.valid = False
        # Hierarchy tracking for section (heading) groups and list items
        self.parents: dict[int, Optional[NodeItem]] = {}
        self.max_levels = 10
        for i in range(-1, self.max_levels):
            self.parents[i] = None
        self.current_section_group = None
        self.current_list_group = None
        self.current_list_item = None
        self._seen_section_texts: set[str] = set()
        # 리스트 중첩 관리를 위한 스택 [(NodeItem, indent_level), …]
        self.list_stack: List[tuple[NodeItem, int]] = []
        self.current_indent: int = 0
        # Open the HWPX zip file
        try:
            if isinstance(path_or_stream, BytesIO):
                self.zip = zipfile.ZipFile(path_or_stream)
            elif isinstance(path_or_stream, Path):
                self.zip = zipfile.ZipFile(str(path_or_stream))
            if "Contents/section0.xml" in self.zip.namelist():
                self.valid = True
        except Exception as e:
            self.valid = False
            raise RuntimeError(f"Failed to open HWPX document: {e}")

    def _extract_text(self, elem: etree._Element) -> str:
        """hp:t 요소에서 tab, fwSpace를 공백으로 치환하면서 텍스트를 뽑아냄"""
        parts: List[str] = []
        if elem.text:
            parts.append(elem.text)
        for inline in elem:
            tag = etree.QName(inline).localname
            if tag in ("tab", "fwSpace","linesegarray"):
                parts.append(" ")
                # print(parts)
            if inline.tail:
                parts.append(inline.tail)
                # print("not tag:", tag, "tail:", inline.tail, parts)
        return "".join(parts).strip()

    def is_valid(self) -> bool:
        return self.valid
    
    @classmethod
    def supported_formats(cls) -> set:
        return {InputFormat.XML_HWPX}
    
    @classmethod
    def supports_pagination(cls) -> bool:
        return False
    
    def unload(self) -> None:
        if self.zip:
            self.zip.close()
            self.zip = None
    

    def _is_toc_numbered_entry(self, t_elem: etree._Element) -> bool:
        """
        숫자+점 패턴이긴 하지만,
        ◇ TOC 항목인지(탭 뒤에 페이지 번호가 붙어 있는지) 검사.
        ex)
        <hp:t>3. 제목<hp:tab .../>9</hp:t>
        """
        # (1) t_elem 바로 아래에 hp:tab 이 있는지
        tabs = t_elem.findall("hp:tab", namespaces=t_elem.nsmap)
        if not tabs:
            return False

        # (2) 각 tab 의 tail 이 숫자로 시작하는지
        for tab in tabs:
            tail = (tab.tail or "").lstrip()
            if re.match(r"^\d+", tail):
                return True

        return False
    
    def _get_image_ref(self, pic_elem: etree._Element) -> Optional[ImageRef]:
        """ hc:img 태그에서 binaryItemIDRef 읽기 """
        img_ref = pic_elem.find("hc:img", namespaces=pic_elem.nsmap)
        if img_ref is None:
            return None
        bin_id = img_ref.get("binaryItemIDRef")
        if not bin_id:
            return None

        # Zip에서 .bmp 포함 모든 확장자로 시도
        for ext in (".bmp", ".png", ".jpg", ".jpeg"):
            try:
                img_bytes = self.zip.read(f"BinData/{bin_id}{ext}")
            except KeyError:
                continue
            # 읽었다면 PIL로 열고
            try:
                pil_img = Image.open(BytesIO(img_bytes))
            except (UnidentifiedImageError, OSError):
                return None
            # ImageRef.from_pil로 포장해 리턴
            return ImageRef.from_pil(image=pil_img, dpi=72)
        return None

    def convert(self) -> DoclingDocument:
        """Parses the HWPX file into a DoclingDocument structure."""
        if not self.is_valid():
            raise RuntimeError("Invalid or unsupported HWPX document")
        origin = DocumentOrigin(
            filename=self.file.name or "file",
            mimetype="application/zip",
            binary_hash=self.document_hash
        )
        doc = DoclingDocument(name=self.file.stem or "file", origin=origin)
        # Create a root group as the top-level parent for all content
        root_group = doc.add_group(parent=None, label=GroupLabel.SECTION, name="root")
        self.parents[0] = root_group
        self.current_section_group = root_group
        section_index = 0
        while True:
            section_path = f"Contents/section{section_index}.xml"
            if section_path not in self.zip.namelist():
                break
            section_xml = self.zip.read(section_path)
            section_root = etree.fromstring(section_xml)
            for elem in section_root:
                if not isinstance(elem, etree._Element):
                    continue
                tag_name = etree.QName(elem).localname
       
                if tag_name == "p":
                    self._process_paragraph(elem, doc)
                elif tag_name == "tbl":
                    self._process_table(elem, doc)
                # elif tag_name == "rect":
                #     self._process_rect(elem, doc)
                # elif tag_name == "pic":
                #     self._process_picture(elem, doc)
                # elif tag_name == "equation":
                #     self._process_equation(elem, doc)
            section_index += 1
        # Close any open list group at end of document
        self._end_list()
        return doc

    def _process_paragraph(self, p_elem: etree._Element, doc: DoclingDocument) -> None:
        # ── (0) secPr 전용 문단: hp:secPr은 있지만 hp:t(text)는 전혀 없으면 “메타데이터” 이므로 스킵
        has_secPr = p_elem.find(".//hp:secPr", namespaces=p_elem.nsmap) is not None
        has_text = p_elem.find(".//hp:run/hp:t", namespaces=p_elem.nsmap) is not None
        if has_secPr and not has_text:
            return
        parents = [etree.QName(x).localname for x in p_elem.iterancestors()]

        # ── 1) 헤더 감지 (A/B 로직) ──
        header_found = False
        header_level = None
        header_text  = None
        for run in p_elem.findall("hp:run", namespaces=p_elem.nsmap):
            t_elem = run.find("hp:t", namespaces=run.nsmap)
            if t_elem is not None:
                txt = self._extract_text(t_elem)
                if re.match(r'^(?:\d+|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+)\.\s+', txt) and not self._is_toc_numbered_entry(t_elem) and txt not in self._seen_section_texts:
                    header_found   = True
                    header_level   = 1
                    header_text    = txt
                    break
            for child in run:
                if etree.QName(child).localname == "tbl" and not self._is_toc_numbered_entry(child):
                    rc = child.get("rowCnt")
                    rows = int(rc) if rc is not None else len(child.findall("hp:tr", namespaces=child.nsmap))
                    cc = child.get("colCnt")
                    cols = int(cc) if cc is not None else len(child.find("hp:tr", namespaces=child.nsmap)
                                                                    .findall("hp:tc", namespaces=child.nsmap))
                    if (rows, cols) in [(1,1),(1,2),(3,1)]:
                        parts = [self._extract_text(t0) for t0 in child.findall(".//hp:t", namespaces=child.nsmap)]
                        txt = "".join(parts).strip()
                        if txt and len(txt)<=100 and txt not in self._seen_section_texts:
                            header_found = True
                            header_level = 1 if rows==1 else 2
                            header_text = txt
                            break
                if etree.QName(child).localname == "rect":
                    draw_text_elem = child.find(".//hp:drawText", namespaces=child.nsmap)
                    if draw_text_elem is None:
                        return
                    text_parts = [t.text for t in draw_text_elem.findall(".//hp:t", namespaces=draw_text_elem.nsmap) if t.text]
                    full_text = "".join(text_parts).strip()
                    norm_text = "".join(full_text.split())
                    if not full_text:
                        continue
                    if len(full_text) <= 100:
                        if not hasattr(self, "_seen_section_texts"):
                            self._seen_section_texts = set()
                            
                        if norm_text in self._seen_section_texts:
                           continue
                        header_text = full_text
                        header_level = 1
                        header_found = True                   
            if header_found:
                break

        if header_found:
            self._seen_section_texts.add(header_text)
            self._end_list()
            self._add_header(doc, header_level, header_text)
            self.current_section_group = self.parents[header_level]
            return
            
             
        # ── 2) 셀 내부(tc)이면서 중첩 테이블이 run 안에 있는 경우 ──       
        if "tc" in parents:
            runs = p_elem.findall("hp:run", namespaces=p_elem.nsmap)

            # 2.1) 모든 run 안의 inline 요소(flatten) 수집
            inlines = []
            for ri, run in enumerate(runs):
                for inline in run:
                    inlines.append((ri, inline))
            # 2.2) 첫 번째 중첩 <hp:tbl> 위치 찾기
            nested_idx = next(
                (i for i, (_, elem) in enumerate(inlines)
                if etree.QName(elem).localname == "tbl"),
                None
            )

            if nested_idx is not None: 
                parent_node = self.current_list_item or self.current_section_group

                # ── 2.3) pre-content: nested 테이블 이전의 inlines 처리 ──
                for i, (ri, elem) in enumerate(inlines[:nested_idx]):
                    tag = etree.QName(elem).localname
                    if tag == "t":
                        txt = self._extract_text(elem).strip()
                        if not txt and not self._is_toc_numbered_entry(elem):
                            continue
                        # 리스트 감지
                        if re.match(r'^(?:□|[①-⑳]|o|\*)', txt):
                            if self.current_list_group is None:
                                self._end_list()
                                self.current_list_group = doc.add_group(
                                    label=GroupLabel.LIST, name="ul",
                                    parent=self.current_section_group
                                )
                            self.current_list_item = doc.add_text(
                                label=DocItemLabel.PARAGRAPH,
                                text=txt,
                                parent=self.current_list_group
                            )
                        else:
                            self._end_list()
                            doc.add_text(
                                label=DocItemLabel.PARAGRAPH,
                                text=txt,
                                parent=parent_node
                            )
                    elif tag == "pic":
                        self._process_picture(elem, doc)
                    elif tag == "rect":
                        self._process_rect(elem, doc)
                    elif tag == "equation":
                        self._process_equation(elem, doc)

                # ── 2.4) nested table 처리 ──
                _, tbl_elem = inlines[nested_idx]
                self._process_table(tbl_elem, doc)

                # ── 2.5) post-content: nested 이후의 inlines 처리 ──
                for j, (ri, elem) in enumerate(inlines[nested_idx+1:], start=nested_idx+1):
                    tag = etree.QName(elem).localname
                    # print(f"[DBG POST] inline #{j} tag={tag}")
                    if tag == "t":
                        txt = self._extract_text(elem).strip()
                        if txt:
                            doc.add_text(
                                label=DocItemLabel.PARAGRAPH,
                                text=txt,
                                parent=parent_node
                            )
                    elif tag == "pic":
                        self._process_picture(elem, doc)
                    elif tag == "rect":
                        self._process_rect(elem, doc)
                    elif tag == "equation":
                        self._process_equation(elem, doc)

                # ── 2.6) 열려 있는 리스트 닫기 ──
                if self.current_list_group and self.current_list_item is None:
                    self._end_list()

                return
            

        # ── 4) 기본 본문 누적 ──
        parent_node = self.current_list_item or self.current_section_group
        # print(f"[DBG BRANCH] FALLTHROUGH to BASE_ACCUMULATION")
        text_buffer = ""
        # p_elem: <hp:p> element
        runs = p_elem.findall(".//hp:run", namespaces=p_elem.nsmap)

        # 모든 child 요소를 flat list로 모아두기
        children = []
        for run in runs:
            children.extend(list(run))

        seen = set()
        i = 0
        while i < len(children):
            child = children[i]
            cid = id(child)
            i += 1

            # 이미 처리한 child면 건너뛰기
            if cid in seen:
                continue
            seen.add(cid)

            tag = etree.QName(child).localname
            if tag == "t":
                text_buffer += (child.text or "")
                
                for inline in child:
                    if etree.QName(inline).localname in ("tab", "fwSpace", "lineBreak"):
                        text_buffer += " "
                    if inline.tail:
                        text_buffer += inline.tail

            elif tag == "tbl":
                # 테이블 처리
                self._process_table(child, doc)
                # 테이블 내부의 모든 요소 ID를 seen에 추가하여 스킵
                for desc in child.iter():
                    seen.add(id(desc))
                continue

            elif tag == "rect":
                self._process_rect(child, doc)
                if child.tail:
                    text_buffer += child.tail

            elif tag == "pic":
                self._process_picture(child, doc)
                if child.tail:
                    text_buffer += child.tail

            elif tag == "equation":
                self._process_equation(child, doc)
                if child.tail:
                    text_buffer += child.tail

        final_text = text_buffer.rstrip()
        # ── 3) 리스트 감지 (셀 내부(tc) 아닌 경우) ──
        full_text = final_text
        if re.match(r'^(?:□|[①-⑳])', full_text):
            self._end_list()
            self.current_list_group = doc.add_group(
                label=GroupLabel.LIST,
                name="ul",
                parent=self.current_section_group
            )
            self.current_list_item = doc.add_text(
                label=DocItemLabel.PARAGRAPH,
                text=full_text.rstrip(),
                parent=self.current_list_group
            )
            return
        
        if self.current_list_group and self.current_list_item is None:
            self._end_list()
            
        if final_text:
            doc.add_text(
                label=DocItemLabel.PARAGRAPH,
                text=final_text,
                parent=parent_node
            )


    def _process_table(self, tbl_elem: etree._Element, doc: DoclingDocument) -> None:
        """ Process a <hp:tbl> element and extract its content into a TableData object."""
        # 0) TOC 감지
        toc = False
        for t in tbl_elem.findall(".//hp:t", namespaces=tbl_elem.nsmap):
            if self._is_toc_numbered_entry(t):
                toc = True
                for p in tbl_elem.findall(".//hp:p", namespaces=tbl_elem.nsmap):
                    self._process_paragraph(p, doc)
                return
    
        parent = self.current_list_item or self.current_section_group or None

        # 1) 크기 파싱
        try:
            num_rows = int(tbl_elem.get("rowCnt","0"))
            num_cols = int(tbl_elem.get("colCnt","0"))
        except ValueError:
            trs = tbl_elem.findall("hp:tr", namespaces=tbl_elem.nsmap)
            num_rows = len(trs)
            num_cols = len(trs[0].findall("hp:tc", namespaces=tbl_elem.nsmap)) if trs else 0
        
        # ── 1a) 작거나 단순한 표를 헤더로 간주 ──
        if (num_rows, num_cols) in [(1,1), (1,2), (3,1)]:
            # 표 내부의 모든 텍스트 조합
            parts = [
                self._extract_text(t0)
                for t0 in tbl_elem.findall(".//hp:t", namespaces=tbl_elem.nsmap)
            ]
            txt = "".join(parts).strip()        
            has_pic = any(
                etree.QName(e).localname in "pic"
                for e in tbl_elem.iter()
            ) 
                        
            if txt and (len(txt) <= 100) and (txt not in self._seen_section_texts):
                # 헤더로 처리
                # print("process_table: txt=", txt[:30], "…")
                self._seen_section_texts.add(txt)
                self._end_list()
                # 1행짜리는 level=1, 3행짜리는 level=2 로 예시
                level = 1 if num_rows == 1 else 2
                self._add_header(doc, level, txt)
                self.current_section_group = self.parents[level]
                if has_pic:
                    # 표 안에 그림이 있으면, 그림도 추가
                    for pic in tbl_elem.findall(".//hp:pic", namespaces=tbl_elem.nsmap):
                        img_ref = self._get_image_ref(pic)
                        if img_ref:
                            doc.add_picture(
                                parent=self.current_section_group,
                                image=img_ref,
                                caption=None
                            )
                return 
     
                        
        data = TableData(num_rows=num_rows, num_cols=num_cols)
        occupied = [[False]*num_cols for _ in range(num_rows)]

        # 2) 위치별 정보 수집
        cell_items = defaultdict(list)   # (r,c) -> [ ('caption', str), ('paragraph', Element), ('table', Element), … ]
        caption_map   = {}                      # (r,c) -> TextItem
        skip_caption  = set()                   # nested 처리된 셀 좌표를 담을 집합 
        rows = tbl_elem.findall("hp:tr", namespaces=tbl_elem.nsmap)

        for r_idx, tr in enumerate(rows):
            for tc in tr.findall("hp:tc", namespaces=tbl_elem.nsmap):
                addr = tc.find("hp:cellAddr", namespaces=tc.nsmap)
                span = tc.find("hp:cellSpan", namespaces=tc.nsmap)
                if addr is None or span is None:
                    continue

                r = int(addr.get("rowAddr"))
                c = int(addr.get("colAddr"))
                rs = int(span.get("rowSpan"))
                cs = int(span.get("colSpan"))

                # 중복 방지
                if occupied[r][c]:
                    continue
                for rr in range(r, r+rs):
                    for cc in range(c, c+cs):
                        occupied[rr][cc] = True

                nested_in_this = bool(tc.findall(".//hp:tbl", namespaces=tc.nsmap))

                # 2) 캡션 감지 전에, 이미 processed_cells에 있다면 스킵
                if (r,c) in skip_caption:
                    continue   
                # 2-a) caption 감지: “다음 행에 nested or pic” 있으면
                next_nested = False
                next_pic    = False
                if r_idx+1 < len(rows):
                    for tc2 in rows[r_idx+1].findall("hp:tc", namespaces=tbl_elem.nsmap):
                        if tc2.findall(".//hp:tbl", namespaces=tc2.nsmap):
                            next_nested = True
                        if tc2.findall(".//hp:pic", namespaces=tc2.nsmap):
                            next_pic = True
                                                             
                if not nested_in_this and (next_nested or next_pic):
                    title = "".join(self._extract_text(t) for t in tc.findall(".//hp:t", namespaces=tc.nsmap)).strip()
                    cell_items[(r,c)].append(('caption', title))
                    # print(f"[DBG BRANCH] caption at ({r},{c})", title)
                    continue
                
                # 2-b) nested table 감지
                if nested_in_this and not toc:
                    # 중첩 테이블 앞뒤 p까지 “셀 내부 순서대로” 처리
                    for p in tc.findall("./hp:subList/hp:p", namespaces=tc.nsmap):
                        tbl = p.find(".//hp:tbl", namespaces=p.nsmap)
                        if tbl is not None:
                            cell_items[(r,c)].append(('table', tbl))
                        else:
                            cell_items[(r,c)].append(('paragraph', p))
                    continue

                # 2-c) picture 감지
                pics = tc.findall(".//hp:pic", namespaces=tc.nsmap)
                if pics:
                    cap_node = caption_map.get((r,c))
                    for pic in pics:
                        img = self._get_image_ref(pic)
                        cell_items[(r,c)].append(('picture', (img, cap_node)))
                    continue

                # 2-d) comment 감지 (‘주:’ or ‘자료:’)
                texts = [
                    "".join(self._extract_text(t) for t in p.findall(".//hp:t", namespaces=tc.nsmap)).strip()
                    for p in tc.findall(".//hp:p", namespaces=tc.nsmap)
                ]
                txt = " ".join(filter(None, texts)).strip()
                if re.match(r"^(?:주|자료)\s*[:：]", txt):
                    cell_items[(r,c)].append(('comment', txt))
                    continue

                # 2-e) 순수 데이터 셀 → TableData 로만
                parts = []
                for p in tc.findall(".//hp:p", namespaces=tc.nsmap):
                    for t in p.findall(".//hp:t", namespaces=p.nsmap):
                        parts.append(self._extract_text(t))
                cell_text = "\n".join(parts).strip()
                if len(cell_text) > 150 and not toc:
                    self._process_paragraph(tc, doc)
                    continue
                
                data.table_cells.append(
                    TableCell(
                        text=cell_text,
                        row_span=rs,
                        col_span=cs,
                        start_row_offset_idx=r,
                        end_row_offset_idx=r+rs,
                        start_col_offset_idx=c,
                        end_col_offset_idx=c+cs,
                        column_header=(r==0),
                        row_header=False
                    )
                )
        # 한 table 안에 주석이 포함되어 있는 경우에 사용 
        # 'table' 없이 'comment'만 있는 경우 판별
        has_table = any(
            typ == 'table'
            for items in cell_items.values()
            for typ, _ in items
        )
        has_picture = any(
            typ == 'picture'
            for items in cell_items.values()
            for typ, _ in items
        )
        has_comment = any(
            typ == 'comment'
            for items in cell_items.values()
            for typ, _ in items
        )
        # print(f"[DBG BRANCH] has_table={has_table}, has_comment={has_comment} at ({r},{c})")
        # comment만 있고 nested 테이블이 없으면
        if (not has_table 
            and has_comment 
            and not has_picture 
            and not nested_in_this
            and not toc
        ):
            # 실제 테이블 추가
            is_empty = not any(cell.text for cell in data.table_cells)
            if not is_empty:
                doc.add_table(data=data, parent=parent)
            # comment 출력
            for items in cell_items.values():
                for typ, txt in items:
                    if typ == 'comment':
                        doc.add_text(
                            label=DocItemLabel.CAPTION,
                            text=txt,
                            parent=parent
                        )
            return
        # 4) 마지막에 한 번에 “순수 테이블”만 TableItem 으로 출력
        for c in range(num_cols):
            for r in range(num_rows):
                for typ, payload in cell_items[(r,c)]:
                    if typ == 'caption':
                        doc.add_text(label=DocItemLabel.PARAGRAPH,
                                    text=payload,
                                    parent=parent)
                    elif typ == 'paragraph':
                        self._process_paragraph(payload, doc)
                    elif typ == 'table':
                        self._process_table(payload, doc)
                    elif typ == 'picture':
                        img, cap = payload
                        doc.add_picture(parent=parent,
                                        image=img,
                                        caption=cap)
                    elif typ == 'comment':
                        doc.add_text(label=DocItemLabel.CAPTION,
                                    text=payload,
                                    parent=parent)

        # 빈 테이블은 추가하지 않음
        # 모든 셀에 텍스트가 없으면 빈 테이블로 간주
        is_empty = True
        for i in data.table_cells:
            if i.text:
                is_empty = False
                break
        if is_empty:
            return
        
        # 마지막에 순수 테이블 한 번만
        doc.add_table(data=data, parent=parent)

    def _process_rect(self, rect_elem: etree._Element, doc: DoclingDocument) -> None:
        """Process a top-level <hp:rect> element (text box)."""
        draw_text_elem = rect_elem.find(".//hp:drawText", namespaces=rect_elem.nsmap)
        if draw_text_elem is None:
            return
        text_parts = [t.text for t in draw_text_elem.findall(".//hp:t", namespaces=draw_text_elem.nsmap) if t.text]
        full_text = "".join(text_parts).strip()
        norm_text = "".join(full_text.split())
        if not full_text:
            return
        if len(full_text) <= 100:
            if not hasattr(self, "_seen_section_texts"):
                self._seen_section_texts = set()
                
            if norm_text in self._seen_section_texts:
                return
            self._seen_section_texts.add(norm_text)
            self._end_list()
            self._add_header(doc, 1, full_text)
            self.current_section_group = self.parents[1]
            return 
        else:
            # parent_node = self.current_section_group
            # doc.add_text(label=DocItemLabel.PARAGRAPH, text=full_text, parent=parent_node)
            #     100자 초과 시: drawText 내의 <hp:p> 단락들을 _process_paragraph로 넘기기
            for p in draw_text_elem.findall(".//hp:p", namespaces=draw_text_elem.nsmap):
                # p 안의 모든 run/t/pic/equation은 _process_paragraph에서 다시 분기 처리됩니다.
                self._process_paragraph(p, doc)
    
    def _process_picture(self, pic_elem: etree._Element, doc: DoclingDocument, caption: Optional[str] = None,) -> None:
        """Process a picture <hp:pic> element and add an image node."""
        parent_node = self.current_list_item or self.current_section_group or None
        image_bytes = None

        # 1) hc:img에서 binaryItemIDRef 꺼내기
        img_ref = pic_elem.find("hc:img", namespaces=pic_elem.nsmap)
        if img_ref is not None:
            bin_id = img_ref.get("binaryItemIDRef")
            if bin_id:
                for ext in (".bmp", ".png", ".jpg", ".jpeg", ".wmf"):
                    try:
                        image_bytes = self.zip.read(f"BinData/{bin_id}{ext}")
                        break
                    except KeyError:
                        continue

        # 2) 이미지 유무에 따라 노드 추가
        if image_bytes is None:
            doc.add_picture(parent=parent_node, image=None, caption=None)
        else:
            try:
                pil_image = Image.open(BytesIO(image_bytes))
            except (UnidentifiedImageError, OSError):
                pil_image = None

            if pil_image:
                img_ref_obj = ImageRef.from_pil(image=pil_image, dpi=72)
                # Markdown에서도 data URI로 인라인 표시
                img_ref_obj.mode = ImageRefMode.EMBEDDED
                doc.add_picture(parent=parent_node, image=img_ref_obj, caption=None)
            else:
                doc.add_picture(parent=parent_node, image=None, caption=None)

    
    def _process_equation(self, eq_elem: etree._Element, doc: DoclingDocument) -> None:
        """Process an equation <hp:equation> element by adding its text content."""
        parent_node = self.current_list_item or self.current_section_group or None
        formula_text = "".join(eq_elem.itertext()).strip()
        doc.add_text(label=DocItemLabel.FORMULA, text=formula_text, parent=parent_node)
    
    def _add_header(self, doc: DoclingDocument, level: int, text: str) -> None:
        """Add a heading node of the given level with the specified text."""
        curr_level = level

        # 상위 그룹들 채워 넣기 - 헤더가 뒤에서 나오는 경우 해결 위해 추가됨 
        for lvl in range(0, curr_level):
            if self.parents.get(lvl) is None:
                self.parents[lvl] = doc.add_group(
                    parent=self.parents[lvl - 1] if lvl - 1 >= 0 else None,
                    label=GroupLabel.SECTION,
                    name=f"header-{lvl}"
                )
        # ------------- 
        # Add intermediate groups for skipped levels
        for lvl in range(0, curr_level):
            if self.parents.get(lvl) is None and lvl < curr_level:
                self.parents[lvl] = doc.add_group(parent=(self.parents[lvl-1] if lvl-1 >= 0 else None), label=GroupLabel.SECTION, name=f"header-{lvl}")
        # Clear deeper levels when moving to a higher-level heading
        for lvl in range(curr_level, self.max_levels):
            self.parents[lvl] = None
        parent_node = self.parents[curr_level-1] if curr_level - 1 >= 0 else None
        self.parents[curr_level] = doc.add_heading(parent=parent_node, text=text, level=curr_level)
    
    def _end_list(self) -> None:
        """End the current list grouping (if any)."""
        self.current_list_group = None
        self.current_list_item = None