## 0514 코드

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

logging.basicConfig(
    filename="hwpx_debug.log",
    filemode="w",               # 매번 덮어쓰려면 "w", 누적하려면 "a"
    level=logging.DEBUG,
    format="%(asctime)s %(message)s",
    encoding="utf-8",
)

logger = logging.getLogger(__name__)

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
    
    def _debug_print_t(self, elem: etree._Element, label: str) -> None:
    # elem 내부의 모든 <hp:t> 요소를 찾아서, 태그 없이 텍스트만 출력
        for t in elem.findall(".//hp:t", namespaces=elem.nsmap):
            text = (t.text or "").strip()
            if text:
                print(f"{label} ⇒ {text}")

    def _extract_text(self, elem: etree._Element) -> str:
        """hp:t 요소에서 tab, fwSpace를 공백으로 치환하면서 텍스트를 뽑아냅니다."""
        parts: List[str] = []
        if elem.text:
            parts.append(elem.text)
        for inline in elem:
            tag = etree.QName(inline).localname
            if tag in ("tab", "fwSpace"):
                parts.append(" ")
            if inline.tail:
                parts.append(inline.tail)
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
        # hc:img 태그에서 binaryItemIDRef 읽기
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
                # print(f"[convert] 만난 요소: {tag_name}")
                # self._debug_print_t(elem, "[convert] hp:t")
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
        text_preview = "".join(p_elem.itertext()).strip()[:30].replace("\n"," ")
        print(f"[DBG PARAGRAPH ENTER] text=\"{text_preview}\"")
        # ── 1) 헤더 감지 (기존 A/B 로직) ──
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
            if header_found:
                print(f"[DBG BRANCH] HEADER level={header_level} text=\"{header_text}\"")
                break

        if header_found:
            self._seen_section_texts.add(header_text)
            self._end_list()
            self._add_header(doc, header_level, header_text)
            self.current_section_group = self.parents[header_level]
            return
        
        # # ── INLINE TABLE 감지 (셀 안팎 관계없이) ──
        # # p_elem 바로 밑 run 중에 tbl 이 있으면
        # inline_tbls = [
        #     child for run in p_elem.findall("hp:run", namespaces=p_elem.nsmap)
        #           for child in run
        #           if etree.QName(child).localname == "tbl"
        # ]
        # if inline_tbls:
        #     print(f"[DBG INLINE-TABLE] found {len(inline_tbls)} table(s) in paragraph")
        #     # 열린 리스트 있으면 닫고
        #     if self.current_list_group:
        #         self._end_list()
        #     # 각각 table 처리
        #     for tbl in inline_tbls:
        #         self._process_table(tbl, doc)
        #     return
        
        # ── 2) 셀 내부(tc)이면서 중첩 테이블이 run 안에 있는 경우 ──
        parents = [etree.QName(x).localname for x in p_elem.iterancestors()]
        print(f"[DBG] parents={parents}")
        if "tc" in parents:
            print(f"[DBG] in_cell=True, parents={parents}")
            runs = p_elem.findall("hp:run", namespaces=p_elem.nsmap)

            # 2.1) 모든 run 안의 inline 요소(flatten) 수집
            # inlines: List[ (run_index, element) ]
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
            print(f"[DBG NESTED] in_cell=True, total inlines={len(inlines)}, nested_idx={nested_idx}")

            if nested_idx is not None: # 잠시 주석 
                parent_node = self.current_list_item or self.current_section_group

                # ── 2.3) pre-content: nested 테이블 이전의 inlines 처리 ──
                for i, (ri, elem) in enumerate(inlines[:nested_idx]):
                    tag = etree.QName(elem).localname
                    print(f"[DBG PRE] inline #{i} tag={tag}")
                    if tag == "t":
                        txt = self._extract_text(elem).strip()
                        if not txt:
                            continue
                        # 리스트 감지
                        print(f"[DBG PRE] text=\"{txt}\"")
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
                        print("[DBG PRE] pic")
                        self._process_picture(elem, doc)
                    elif tag == "rect":
                        print("[DBG PRE] rect")
                        self._process_rect(elem, doc)
                    elif tag == "equation":
                        print("[DBG PRE] equation")
                        self._process_equation(elem, doc)

                # ── 2.4) nested table 처리 ──
                _, tbl_elem = inlines[nested_idx]
                print(f"[DBG NESTED] processing nested <tbl> at inline #{nested_idx}")
                self._process_table(tbl_elem, doc)

                # ── 2.5) post-content: nested 이후의 inlines 처리 ──
                for j, (ri, elem) in enumerate(inlines[nested_idx+1:], start=nested_idx+1):
                    tag = etree.QName(elem).localname
                    print(f"[DBG POST] inline #{j} tag={tag}")
                    if tag == "t":
                        txt = self._extract_text(elem).strip()
                        if txt:
                            doc.add_text(
                                label=DocItemLabel.PARAGRAPH,
                                text=txt,
                                parent=parent_node
                            )
                    elif tag == "pic":
                        print("[DBG POST] pic")
                        self._process_picture(elem, doc)
                    elif tag == "rect":
                        print("[DBG POST] rect")
                        self._process_rect(elem, doc)
                    elif tag == "equation":
                        print("[DBG POST] equation")
                        self._process_equation(elem, doc)

                # ── 2.6) 열려 있는 리스트 닫기 ──
                if self.current_list_group and self.current_list_item is None:
                    print("[DBG NESTED] closing open list")
                    self._end_list()

                return
        
        # ── 3) 리스트 감지 (셀 내부(tc) 아닌 경우) ──
        print("list 감지")
        full_text = "".join(p_elem.itertext()).strip()
        # print(f"[DBG] full_text={full_text}")
        in_cell   = "tc" in parents
        if not in_cell and re.match(r'^(?:□|[①-⑳])', full_text):
            print(f"[DBG BRANCH] LIST detected text=\"{full_text[:30]}\"")
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

        # ── 4) 기본 본문 누적 ──
        parent_node = self.current_list_item or self.current_section_group
        print(f"[DBG BRANCH] FALLTHROUGH to BASE_ACCUMULATION")
        text_buffer = ""
        for run in p_elem.findall(".//hp:run", namespaces=p_elem.nsmap):
            for child in run:
                tag = etree.QName(child).localname
                if tag == "t":
                    text_buffer += (child.text or "")
                    for inline in child:
                        if etree.QName(inline).localname in ("tab","fwSpace","lineBreak"):
                            text_buffer += " "
                        if inline.tail:
                            text_buffer += inline.tail
                elif tag == "tbl":
                    print(f"[DBG INLINE] found inline <tbl> – will call _process_table")
                    self._process_table(child, doc)
                    if child.tail:
                        text_buffer += child.tail
                    return
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
        if final_text:
            print(f"[DBG BASE] adding Paragraph text=\"{final_text[:30]}\"")
            doc.add_text(
                label=DocItemLabel.PARAGRAPH,
                text=final_text,
                parent=parent_node
            )


    def _process_table(self, tbl_elem: etree._Element, doc: DoclingDocument) -> None:
        print("[DBG] _process_table")
        """Process a table <hp:tbl> element, adding it as a TableData node.
        But first detect TOC (차례) tables and output paragraphs only."""

        # colAddr → header text 매핑
        caption_map: dict[int,str] = {}
        
        # ── 0) TOC(table of contents) 감지 ──
        for t_elem in tbl_elem.findall(".//hp:t", namespaces=tbl_elem.nsmap):
            # 탭 뒤에 페이지 번호가 붙어 있으면 TOC 항목
            if self._is_toc_numbered_entry(t_elem):
                # TOC는 표 셀로 만들지 말고 단락으로만 처리
                for p in tbl_elem.findall(".//hp:p", namespaces=tbl_elem.nsmap):
                    self._process_paragraph(p, doc)
                return
            
        parent_node = self.current_list_item or self.current_section_group or None

        # 1) 테이블 크기 파싱
        try:
            num_rows = int(tbl_elem.get("rowCnt","0"))
            num_cols = int(tbl_elem.get("colCnt","0"))
        except ValueError:
            rows = tbl_elem.findall("hp:tr", namespaces=tbl_elem.nsmap)
            num_rows = len(rows)
            num_cols = len(rows[0].findall("hp:tc", namespaces=tbl_elem.nsmap)) if rows else 0

        data = TableData(num_rows=num_rows, num_cols=num_cols)
        occupied = [[False]*num_cols for _ in range(num_rows)]

        # 2) 각 셀(tc) 순회
        rows = tbl_elem.findall("hp:tr", namespaces=tbl_elem.nsmap)
        #---주석 처리 부분---
        comment_map: dict[int, str] = {}
        for row_idx, tr in enumerate(rows):
            # # ---주석 처리 부분 ---
            # # 1) comment-only row 감지
            # #    (예: '주 : …' 로 시작하는 텍스트만 포함된 행)
            # cells = tr.findall("hp:tc", namespaces=tbl_elem.nsmap)
            # texts = [
            #     "".join(self._extract_text(t) for t in tc.findall(".//hp:t", namespaces=tc.nsmap)).strip()
            #     for tc in cells
            # ]
            # if row_idx > 0 and texts and all(re.match(r"^\s*(?:주|자료)\s*[:：]", txt) for txt in texts if txt):
            #     for tc, txt in zip(cells, texts):
            #         col = int(tc.find("hp:cellAddr", namespaces=tc.nsmap).get("colAddr"))
            #         comment_map[col] = txt
            #     # 이 행은 테이블 데이터로 만들지 않고 건너뛰기
            #     continue
            # # ---주석 처리 부분 --- 
            for tc in tr.findall("hp:tc", namespaces=tbl_elem.nsmap):
                addr = tc.find("hp:cellAddr", namespaces=tc.nsmap)
                span = tc.find("hp:cellSpan", namespaces=tc.nsmap)
                if addr is None or span is None:
                    continue
                row = int(addr.get("rowAddr","0"))
                col = int(addr.get("colAddr","0"))
                row_span = int(span.get("rowSpan","1"))
                col_span = int(span.get("colSpan","1"))
                # 중복 방지
                if occupied[row][col]:
                    continue
                for r in range(row, row+row_span):
                    for c in range(col, col+col_span):
                        occupied[r][c] = True

                # ── Caption-row 검사 ──
                nested_in_cell = bool(tc.findall(".//hp:tbl", namespaces=tc.nsmap))
                next_has_nested = False
                next_has_pic = False
                if row_idx + 1 < len(rows):
                    next_row = rows[row_idx+1]
                    for tc2 in next_row.findall("hp:tc", namespaces=tbl_elem.nsmap):
                        if tc2.findall(".//hp:tbl", namespaces=tc2.nsmap):
                            next_has_nested = True
                            break
                        if tc2.findall(".//hp:pic", namespaces=tc2.nsmap): # 그림이 있으면 
                            next_has_pic = True
                            break
                # if row_idx == 0 and not nested_in_cell and (next_has_nested or next_has_pic): # pic 추가 
                #     for p in tc.findall(".//hp:p", namespaces=tc.nsmap):
                #         self._process_paragraph(p, doc)
                #     continue

                # # ── Image-row 검사 ──
                # # (캡션 다음 셀에 그림만 들어 있을 때)
                # pics = tc.findall(".//hp:pic", namespaces=tc.nsmap)
                # if pics and not nested_in_cell:
                #     # 앞서 뽑은 caption_cell 에 매달린 그림이 되도록
                #     for pic_elem in pics:
                #         self._process_picture(pic_elem, doc)
                #     continue
                # ── Caption-row 검사 ──
                if row_idx == 0 and not nested_in_cell and (next_has_nested or next_has_pic):
                    # 제목 셀 텍스트를 캡션 맵에 저장
                    # caption_text = "".join(
                    #     self._extract_text(t) for t in tc.findall(".//hp:t", namespaces=tc.nsmap)
                    # ).strip()
                    cap_node = doc.add_text(
                        label=DocItemLabel.PARAGRAPH,  # 또는 PARAGRAPH
                        text="".join(
                            self._extract_text(t)
                            for t in tc.findall(".//hp:t", namespaces=tc.nsmap)
                        ).strip(),
                        parent=parent_node
                    )
                    caption_map[col] = cap_node
                    # 그리고 단순히 paragraph 로 흘려보내서 제목으로 출력
                    # for p in tc.findall(".//hp:p", namespaces=tc.nsmap):
                    #     self._process_paragraph(p, doc)
                    continue

                # ── Image-row 검사 ──
                # (캡션 바로 아래, 같은 colAddr 에 그림만 들어 있을 때)
                pics = tc.findall(".//hp:pic", namespaces=tc.nsmap)
                if pics and not nested_in_cell:
                    # # caption_map 에 저장한 텍스트를 caption 으로 넘겨줍니다
                    # caption_text = caption_map.get(col)
                    # cap_node = None
                    # if caption_text:
                    # # 캡션용 TextItem 생성
                    #     cap_node = doc.add_text(
                    #         label=DocItemLabel.PARAGRAPH,  # 필요시 DocItemLabel.CAPTION 사용
                    #         text=caption_text,
                    #         parent=parent_node
                    #     )
                    # for pic_elem in pics:
                    #     # _process_picture 에 caption 인자를 추가하거나,
                    #     # 아니면 process 후 바로 caption paragraph 를 달아주셔도 됩니다.
                    #     img_ref = self._get_image_ref(pic_elem)
                    #     doc.add_picture(parent=parent_node, image=img_ref, caption=cap_node)
                    # continue

                    cap_node = caption_map.get(col)
                    for pic_elem in pics:
                        img_ref = self._get_image_ref(pic_elem)
                        doc.add_picture(
                            parent=parent_node,
                            image=img_ref,
                            caption=cap_node
                        )
                    continue
                    # # 같은 col 에 저장해둔 cap_node(NodeItem)를 꺼내서
                    # cap_node = caption_map.get(col)
                    # for pic_elem in pics:
                    #     # 1) 이미지 바이트 -> ImageRef
                    #     img_ref = self._get_image_ref(pic_elem)
                    #     # 2) 원래 로직대로 _process_picture 로 추가 (이 안에서 doc.add_picture 이 호출됨)
                    #     pic_item = self._process_picture(pic_elem, doc)
                    #     # 3) caption 이 있으면 NodeItem.get_ref() 로 붙여준다
                    #     if cap_node and pic_item:
                    #         pic_item.captions.append(cap_node.get_ref())
                    # continue
                if nested_in_cell:
                    # 3a) nested 전 hp:p 단락 처리
                    print("3a 분기 탐 ")
                    # for p in tc.findall(".//hp:p", namespaces=tc.nsmap):
                    for p in tc.findall("./hp:subList/hp:p", namespaces=tc.nsmap): # 오류나면 아래 for 문 써보기기
                    # paras = tc.findall("./hp:subList/hp:p | ./hp:p", namespaces=ns) # sublist없을 때 방지  
                    # for p in paras:

                        # if p.find(".//hp:tbl", namespaces=p.nsmap) is not None: # p안에 nested table이 있으면 process_paragraph으로 p를 보낸다 
                        #     break
                        # self._process_paragraph(p, doc)
                        if p.find("hp:tbl", namespaces=p.nsmap):
                            # 이 p 안의 테이블만
                            print(f"[DBG BRANCH] nesting table at p with text='{ ''.join(p.itertext()).strip()[:30] }…'")
                            self._process_table(p.find("hp:tbl", namespaces=p.nsmap), doc)
                        else:
                            # 이 p 안의 텍스트·리스트·수식 등 모두
                            print(f"[DBG BRANCH] paragraph at p with text='{ ''.join(p.itertext()).strip()[:30] }…'")
                            self._process_paragraph(p, doc)
                    # # 3b) nested table 들 처리
                    # for nested in nested_in_cell:
                    #     self._process_table(nested, doc)
                    continue

                # 4) 진짜 순수 테이블 셀: 텍스트 수집 후 TableCell 생성
                # print("pure table cell")
                parts: List[str] = []
                for para in tc.findall(".//hp:p", namespaces=tc.nsmap):
                    for t in para.findall(".//hp:t", namespaces=para.nsmap):
                        parts.append(self._extract_text(t))
                cell_text = "\n".join(parts).strip()

                column_header = (row == 0)
                table_cell = TableCell(
                    text=cell_text,
                    row_span=row_span,
                    col_span=col_span,
                    start_row_offset_idx=row,
                    end_row_offset_idx=row+row_span,
                    start_col_offset_idx=col,
                    end_col_offset_idx=col+col_span,
                    column_header=column_header,
                    row_header=False
                )
                data.table_cells.append(table_cell)

        # 5) 테이블 노드로 추가
        doc.add_table(data=data, parent=parent_node)
        # 6) comment_map 에 담긴 각 컬럼별 주석을
        #    테이블 바로 아래 단락으로 내보냅니다.
        for col, comment_text in comment_map.items():
            # same parent_node 아래에 단락으로 추가
            doc.add_text(
                label=DocItemLabel.PARAGRAPH,  
                text=comment_text,
                parent=parent_node
            )

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
        if len(full_text) <= 100 and "여백" not in norm_text:
            if not hasattr(self, "_seen_section_texts"):
                self._seen_section_texts = set()
            if norm_text in self._seen_section_texts:
                return
            self._seen_section_texts.add(norm_text)
            self._end_list()
            self._add_header(doc, 1, full_text)
            self.current_section_group = self.parents[1]
        else:
            # parent_node = self.current_section_group
            # doc.add_text(label=DocItemLabel.PARAGRAPH, text=full_text, parent=parent_node)
                # 100자 초과 시: drawText 내의 <hp:p> 단락들을 _process_paragraph로 넘기기
            for p in draw_text_elem.findall(".//hp:p", namespaces=draw_text_elem.nsmap):
                # p 안의 모든 run/t/pic/equation은 _process_paragraph에서 다시 분기 처리됩니다.
                self._process_paragraph(p, doc)
    
    # def _process_picture(self, pic_elem: etree._Element, doc: DoclingDocument) -> None:
    #     """Process a picture <hp:pic> element and add an image node."""
    #     parent_node = self.current_list_item or self.current_section_group or None
    #     image_bytes = None
    #     img_ref = pic_elem.find("hc:img", namespaces=pic_elem.nsmap)
    #     if img_ref is not None:
    #         bin_id = img_ref.get("binaryItemIDRef")
    #         if bin_id:
    #             for ext in (".bmp", ".png", ".jpg", ".jpeg"):
    #                 try:
    #                     image_bytes = self.zip.read(f"BinData/{bin_id}{ext}")
    #                     break
    #                 except KeyError:
    #                     image_bytes = None
    #                     continue
    #     if image_bytes is None:
    #         doc.add_picture(parent=parent_node, image=None, caption=None)
    #     else:
    #         try:
    #             pil_image = Image.open(BytesIO(image_bytes))
    #         except (UnidentifiedImageError, OSError):
    #             pil_image = None
    #         if pil_image:
    #             img_ref_obj = ImageRef.from_pil(image=pil_image, dpi=72)
    #             doc.add_picture(parent=parent_node, image=img_ref_obj, caption=None)
    #         else:
    #             doc.add_picture(parent=parent_node, image=None, caption=None)
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
        print(f"[ADD_HEADER] level={level}, text={text}")
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
        # print(f"Added header: {text} at level {curr_level}")
    
    def _end_list(self) -> None:
        """End the current list grouping (if any)."""
        self.current_list_group = None
        self.current_list_item = None
        # self.list_stack.clear() # 추가해봤다 