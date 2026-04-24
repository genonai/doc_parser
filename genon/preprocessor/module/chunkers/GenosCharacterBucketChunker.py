import math, bisect
from pathlib import Path
from typing import Any, Iterator, List, Optional, Union
from typing_extensions import Self

from docling_core.types import DoclingDocument
from docling_core.types.doc import DocItem, TextItem, SectionHeaderItem, CodeItem, TableItem, PictureItem
from docling_core.types.doc.document import LevelNumber, ContentLayer, ListItem
from docling_core.transforms.chunker import BaseChunker, BaseChunk, DocChunk, DocMeta
from docling_core.types.doc.labels import DocItemLabel


class GenosCharacterBucketChunker(BaseChunker):
    """문자 수 제한을 고려하여 섹션별 청크를 분할하고 병합하는 청커"""

    """각 item의 경우 의미 보존을 위해서 max_chars를 넘더라도 분할하지 않음"""

    max_chars: int = 4096
    merge_peers: bool = True
    merge_list_items: bool = True

    def _count_chars(self, text: str) -> int:
        """텍스트의 문자 수 반환"""
        return len(text) if text else 0

    def preprocess(self, dl_doc: DoclingDocument, **kwargs: Any) -> Iterator[BaseChunk]:
        all_items = []
        all_header_info = []
        current_heading_by_level: dict[LevelNumber, str] = {}
        all_header_short_info = []
        current_heading_short_by_level: dict[LevelNumber, str] = {}
        list_items: list[TextItem] = []

        processed_refs = set()

        for item, level in dl_doc.iterate_items(included_content_layers={ContentLayer.BODY, ContentLayer.FURNITURE}):
            if hasattr(item, "self_ref"):
                processed_refs.add(item.self_ref)

            if not isinstance(item, DocItem):
                continue

            if self.merge_list_items:
                if isinstance(item, ListItem) or (isinstance(item, TextItem) and item.label == DocItemLabel.LIST_ITEM):
                    list_items.append(item)
                    continue
                elif list_items:
                    for list_item in list_items:
                        all_items.append(list_item)
                        all_header_info.append({k: v for k, v in current_heading_by_level.items()})
                        all_header_short_info.append({k: v for k, v in current_heading_short_by_level.items()})
                    list_items = []

            if isinstance(item, SectionHeaderItem) or (
                isinstance(item, TextItem) and item.label in [DocItemLabel.SECTION_HEADER, DocItemLabel.TITLE]
            ):
                header_level = (
                    item.level
                    if isinstance(item, SectionHeaderItem)
                    else (0 if item.label == DocItemLabel.TITLE else 1)
                )
                current_heading_by_level[header_level] = item.text
                current_heading_short_by_level[header_level] = item.orig

                keys_to_del = [k for k in current_heading_by_level if k > header_level]
                for k in keys_to_del:
                    current_heading_by_level.pop(k, None)
                keys_to_del_short = [k for k in current_heading_short_by_level if k > header_level]
                for k in keys_to_del_short:
                    current_heading_short_by_level.pop(k, None)

                all_items.append(item)
                all_header_info.append({k: v for k, v in current_heading_by_level.items()})
                all_header_short_info.append({k: v for k, v in current_heading_short_by_level.items()})
                continue

            if (
                isinstance(item, TextItem)
                or isinstance(item, ListItem)
                or isinstance(item, CodeItem)
                or isinstance(item, TableItem)
                or isinstance(item, PictureItem)
            ):
                all_items.append(item)
                all_header_info.append({k: v for k, v in current_heading_by_level.items()})
                all_header_short_info.append({k: v for k, v in current_heading_short_by_level.items()})

        if list_items:
            for list_item in list_items:
                all_items.append(list_item)
                all_header_info.append({k: v for k, v in current_heading_by_level.items()})
                all_header_short_info.append({k: v for k, v in current_heading_short_by_level.items()})

        missing_tables = []
        for table in dl_doc.tables:
            table_ref = getattr(table, "self_ref", None)
            if table_ref not in processed_refs:
                missing_tables.append(table)

        if missing_tables:
            for missing_table in missing_tables:
                all_items.insert(0, missing_table)
                all_header_info.insert(0, {})
                all_header_short_info.insert(0, {})

        if not all_items:
            return

        chunk = DocChunk(
            text="",
            meta=DocMeta(
                doc_items=all_items,
                headings=None,
                captions=None,
                origin=dl_doc.origin,
            ),
        )
        chunk._header_info_list = all_header_info
        chunk._header_short_info_list = all_header_short_info
        yield chunk

    def _generate_text_from_items_with_headers(
        self, items: list[DocItem], header_info_list: list[dict], dl_doc: DoclingDocument, **kwargs
    ) -> str:
        text_parts = []
        current_section_headers = {}

        for i, item in enumerate(items):
            item_headers = header_info_list[i] if i < len(header_info_list) else {}

            if item_headers != current_section_headers:
                headers_to_add = []
                for level in sorted(item_headers.keys()):
                    if level not in current_section_headers or current_section_headers[level] != item_headers[level]:
                        for l in sorted(item_headers.keys()):
                            if l < level:
                                headers_to_add.append(item_headers[l])
                            elif l == level:
                                headers_to_add.append("")
                        break

                if headers_to_add:
                    header_text = ", ".join(headers_to_add)
                    if header_text not in text_parts:
                        text_parts.append(header_text)

                current_section_headers = item_headers.copy()

            if isinstance(item, TableItem):
                table_text = self._extract_table_text(item, dl_doc, **kwargs)
                if table_text:
                    text_parts.append(table_text)
            elif hasattr(item, "text") and item.text:
                if item.text not in text_parts:
                    text_parts.append(item.text)
            elif isinstance(item, PictureItem):
                text_parts.append("")

        return self.delim.join(text_parts)

    def _extract_table_text(self, table_item: TableItem, dl_doc: DoclingDocument, **kwargs) -> str:
        try:
            export_to_html = kwargs.get("export_to_html", 1)
            if export_to_html == 1:
                table_text = table_item.export_to_html(dl_doc)
            else:
                table_text = table_item.export_to_markdown(dl_doc)
            if table_text and table_text.strip():
                return table_text
        except Exception:
            pass

        try:
            if hasattr(table_item, "data") and table_item.data:
                cell_texts = []

                if hasattr(table_item.data, "table_cells"):
                    for cell in table_item.data.table_cells:
                        if hasattr(cell, "text") and cell.text and cell.text.strip():
                            cell_texts.append(cell.text.strip())

                elif hasattr(table_item.data, "grid") and table_item.data.grid:
                    for row in table_item.data.grid:
                        if isinstance(row, list):
                            for cell in row:
                                if hasattr(cell, "text") and cell.text and cell.text.strip():
                                    cell_texts.append(cell.text.strip())

                if cell_texts:
                    return " ".join(cell_texts)
        except Exception:
            pass

        if hasattr(table_item, "text") and table_item.text:
            return table_item.text

        return ""

    def _extract_used_headers(self, header_info_list: list[dict]) -> Optional[list[str]]:
        if not header_info_list:
            return None

        all_headers = []
        seen_headers = set()

        for header_info in header_info_list:
            if header_info:
                for level in sorted(header_info.keys()):
                    header_text = header_info[level]
                    if header_text and header_text not in seen_headers:
                        all_headers.append(header_text)
                        seen_headers.add(header_text)

        return all_headers if all_headers else None

    def _split_table_text(self, table_text: str, max_chars: int) -> list[str]:
        """테이블 텍스트를 문자 수 제한에 맞게 단순 분할"""
        if not table_text or len(table_text) <= max_chars:
            return [table_text]

        chunks = []
        for start in range(0, len(table_text), max_chars):
            chunks.append(table_text[start:start + max_chars])
        return chunks

    def _is_section_header(self, item: DocItem) -> bool:
        return isinstance(item, SectionHeaderItem) or (
            isinstance(item, TextItem) and item.label in [DocItemLabel.SECTION_HEADER, DocItemLabel.TITLE]
        )

    def _get_section_header_level(self, item: DocItem) -> Optional[int]:
        if isinstance(item, SectionHeaderItem):
            return item.level
        elif isinstance(item, TextItem):
            if item.label == DocItemLabel.TITLE:
                return 0
            elif item.label == DocItemLabel.SECTION_HEADER:
                return 1
        return None

    def _generate_section_text_with_heading(
        self, section_items: list[DocItem], section_header_infos: list[dict], dl_doc: DoclingDocument, **kwargs
    ) -> str:
        if section_header_infos and section_header_infos[0]:
            merged_headers = {}
            for level, header_text in section_header_infos[0].items():
                if header_text:
                    merged_headers[level] = header_text

            if merged_headers:
                sorted_levels = sorted(merged_headers.keys())
                headers = [merged_headers[level] for level in sorted_levels]
                heading_text = ", ".join(headers)
            else:
                heading_text = ""
        else:
            heading_text = ""

        section_text = self._generate_text_from_items_with_headers(
            section_items, section_header_infos, dl_doc, **kwargs
        )

        if heading_text:
            return heading_text + ", " + section_text
        else:
            return section_text

    def _split_document_by_chars(self, doc_chunk: DocChunk, dl_doc: DoclingDocument, **kwargs) -> list[DocChunk]:
        """문서를 문자 수 제한에 맞게 분할 (섹션 헤더 기준 분할 후 max_chars로 병합)"""
        items = doc_chunk.meta.doc_items
        header_info_list = getattr(doc_chunk, "_header_info_list", [])
        header_short_info_list = getattr(doc_chunk, "_header_short_info_list", [])

        if not items:
            return []

        # ================================================================
        # 헬퍼 함수들
        # ================================================================

        def get_header_level(header_infos, *, first=False, default=-1):
            if not header_infos:
                return default
            info = header_infos[0] if first else header_infos[-1]
            return max(info.keys(), default=default)

        def get_current_chunk(
            doc_chunk: DocChunk,
            merged_texts: list[str],
            merged_header_short_infos: list[dict],
            merged_items: list[DocItem],
        ):
            if not merged_texts:
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
                ),
            )

        def get_text_from_item(item: DocItem) -> str:
            if isinstance(item, TableItem):
                return self._extract_table_text(item, dl_doc, **kwargs)
            elif hasattr(item, "text") and item.text:
                return item.text
            elif isinstance(item, PictureItem):
                text = ""
                for annotation in item.annotations:
                    if hasattr(annotation, "text"):
                        text += annotation.text
                return text
            return ""

        def split_items_evenly_by_chars(item_char_counts, max_chars):
            n = len(item_char_counts)
            total = sum(item_char_counts)
            if n == 0:
                return []
            if total <= max_chars:
                return [(0, n)]

            k = math.ceil(total / max_chars)
            target = total / k

            P = [0]
            for c in item_char_counts:
                P.append(P[-1] + c)

            cuts = [0]
            used = {0}
            for t in range(1, k):
                goal = t * target
                j = bisect.bisect_left(P, goal)

                cand = []
                if 0 < j < len(P):
                    cand.append(j)
                if 0 <= j - 1 < len(P):
                    cand.append(j - 1)

                best = None
                best_dist = float("inf")
                for x in cand:
                    if x in used:
                        continue
                    if x <= cuts[-1]:
                        continue
                    if x >= len(P) - 1:
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
                if hasattr(item, "captions") and item.captions:
                    for cap in item.captions:
                        cap_ref = cap.cref
                        cap_idx = -1
                        for j, it in enumerate(items_group):
                            if it is None:
                                continue
                            if getattr(it[0][0], "self_ref", None) == cap_ref:
                                cap_idx = j
                                break
                        if cap_idx != -1:
                            ref_idx_list.append(cap_idx)
                if ref_idx_list:
                    ref_idx_list = sorted(ref_idx_list)

                if not ref_idx_list:
                    continue

                for cap_idx in ref_idx_list:
                    for g in items_group[cap_idx]:
                        items_group[idx].append(g)
                    items_group[cap_idx] = None
                    b_modified = True

            if b_modified:
                items_group = [it for it in items_group if it is not None]

            return items_group

        def adjust_pictures_in_tables(items_group):
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
                            pic_bbox = pic_item.prov[0].bbox
                            pic_page_no = pic_item.prov[0].page_no
                            if pic_page_no != table_page_no:
                                continue
                            ios = pic_bbox.intersection_over_self(table_bbox)
                            if ios > 0.5:
                                pic_idx_list.append(j)
                    if pic_idx_list:
                        pic_idx_list = sorted(pic_idx_list)

                if not pic_idx_list:
                    continue

                for pic_idx in pic_idx_list:
                    for g in items_group[pic_idx]:
                        items_group[idx].append(g)
                    items_group[pic_idx] = None
                    b_modified = True

            if b_modified:
                items_group = [it for it in items_group if it is not None]

            return items_group

        # ================================================================
        # 1단계: 섹션 헤더 기준으로 분할
        # ================================================================

        sections = []
        cur_items, cur_h_infos, cur_h_short = [], [], []

        for i, item in enumerate(items):
            h_info = header_info_list[i] if i < len(header_info_list) else {}
            h_short = header_short_info_list[i] if i < len(header_short_info_list) else {}

            if self._is_section_header(item):
                if cur_items:
                    sections.append((cur_items, cur_h_infos, cur_h_short))

                cur_items = [item]
                cur_h_infos = [h_info]
                cur_h_short = [h_short]
            else:
                cur_items.append(item)
                cur_h_infos.append(h_info)
                cur_h_short.append(h_short)

        if cur_items:
            sections.append((cur_items, cur_h_infos, cur_h_short))

        # ================================================================
        # 2단계: 각 섹션의 텍스트에 heading 붙이기
        # ================================================================

        sections_with_text = []
        for items, header_infos, header_short_infos in sections:
            text = self._generate_section_text_with_heading(items, header_short_infos, dl_doc, **kwargs)
            sections_with_text.append((text, items, header_infos, header_short_infos))

        # ================================================================
        # 2.5단계: 너무 긴 청크는 분할
        # ================================================================
        if self.max_chars > 0:
            for i in range(len(sections_with_text)):
                text, items, h_infos, h_short = sections_with_text[i]
                if self._count_chars(text) < self.max_chars:
                    continue

                items_group = [[(item, info, short)] for item, info, short in zip(items, h_infos, h_short)]
                items_group = adjust_captions(items_group)
                items_group = adjust_pictures_in_tables(items_group)

                item_char_counts = []
                for group in items_group:
                    cur_count = 0
                    for g in group:
                        cur_count += self._count_chars(get_text_from_item(g[0]))
                    item_char_counts.append(cur_count)

                split_info = split_items_evenly_by_chars(item_char_counts, self.max_chars)

                new_sections = []
                for a, b in split_info:
                    group_items = []
                    group_h_infos = []
                    group_h_short = []
                    for idx in range(a, b):
                        for g in items_group[idx]:
                            group_items.append(g[0])
                            group_h_infos.append(g[1])
                            group_h_short.append(g[2])

                    new_text = self._generate_section_text_with_heading(group_items, group_h_short, dl_doc, **kwargs)
                    new_sections.append((new_text, group_items, group_h_infos, group_h_short))

                sections_with_text.pop(i)
                for new_section in reversed(new_sections):
                    sections_with_text.insert(i, new_section)

        # ================================================================
        # 3단계: 단독 타이틀(1줄만) → 다음 섹션으로 병합
        # ================================================================

        for i in range(len(sections_with_text) - 2, -1, -1):
            text, items, h_infos, h_short = sections_with_text[i]

            if len(items) != 1 or not self._is_section_header(items[0]):
                continue

            item_text = "".join(getattr(it, "text", "") for it in items)
            if len(item_text) > 30:
                continue

            n_text, n_items, n_h_infos, n_h_short = sections_with_text[i + 1]
            current_level = get_header_level(h_infos, first=False)
            next_level = get_header_level(n_h_infos, first=True)
            if 0 <= next_level < current_level:
                continue

            sections_with_text[i] = (text + "\n" + n_text, items + n_items, h_infos + n_h_infos, h_short + n_h_short)
            sections_with_text.pop(i + 1)

        # ================================================================
        # 4단계: 문자 수 기준 병합
        # ================================================================

        result_chunks = []
        merged_texts, merged_items = [], []
        merged_header_infos, merged_header_short_infos = [], []

        for text, items, header_infos, header_short_infos in sections_with_text:

            b_new_chunk = False

            test_chars = self._count_chars("\n".join(merged_texts + [text]))

            section_level = get_header_level(header_infos, first=True)
            merged_level = get_header_level(merged_header_infos, first=False)

            if test_chars > self.max_chars and len(merged_texts) > 0:
                b_new_chunk = True
            elif 0 <= section_level < merged_level:
                b_new_chunk = True

            if b_new_chunk:
                cur_chunk = get_current_chunk(doc_chunk, merged_texts, merged_header_short_infos, merged_items)
                if cur_chunk:
                    result_chunks.append(cur_chunk)

                merged_texts = [text]
                merged_items = items
                merged_header_infos = header_infos
                merged_header_short_infos = header_short_infos
            else:
                merged_texts.append(text)
                merged_items.extend(items)
                merged_header_infos.extend(header_infos)
                merged_header_short_infos.extend(header_short_infos)

        cur_chunk = get_current_chunk(doc_chunk, merged_texts, merged_header_short_infos, merged_items)
        if cur_chunk:
            result_chunks.append(cur_chunk)

        return result_chunks

    def chunk(self, dl_doc: DoclingDocument, **kwargs: Any) -> Iterator[BaseChunk]:
        doc_chunks = list(self.preprocess(dl_doc=dl_doc, **kwargs))

        if not doc_chunks:
            return iter([])

        doc_chunk = doc_chunks[0]

        final_chunks = self._split_document_by_chars(doc_chunk, dl_doc, **kwargs)

        return iter(final_chunks)
