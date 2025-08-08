"""Hybrid chunker implementation for document processing"""

from typing import Any, Iterator, List, Optional, Union
from pydantic import BaseModel, model_validator, ConfigDict
from typing_extensions import Self

from docling_core.transforms.chunker import BaseChunk, BaseChunker, DocChunk, DocMeta
from docling_core.types import DoclingDocument
from docling_core.types.doc import (
    DocItem, DocItemLabel, SectionHeaderItem, TableItem, TextItem,
    ListItem, CodeItem, PictureItem
)

try:
    from transformers import AutoTokenizer, PreTrainedTokenizerBase
except ImportError:
    raise RuntimeError(
        "Module requires 'chunking' extra; to install, run: "
        "`pip install 'docling-core[chunking]'`"
    )


class HierarchicalChunker(BaseChunker):
    """Document structure and header hierarchy preserving chunker"""
    
    merge_list_items: bool = True
    
    def chunk(self, dl_doc: DoclingDocument, **kwargs: Any) -> Iterator[BaseChunk]:
        """Chunk the document preserving structure
        
        Args:
            dl_doc: Document to chunk
            
        Yields:
            Chunks with header information
        """
        all_items = []
        all_header_info = []
        current_heading_by_level = {}
        list_items = []
        
        processed_refs = set()
        
        for item, level in dl_doc.iterate_items():
            if hasattr(item, 'self_ref'):
                processed_refs.add(item.self_ref)
            
            if not isinstance(item, DocItem):
                continue
            
            # Handle list item merging
            if self.merge_list_items:
                if isinstance(item, ListItem) or (
                    isinstance(item, TextItem) and item.label == DocItemLabel.LIST_ITEM
                ):
                    list_items.append(item)
                    continue
                elif list_items:
                    for list_item in list_items:
                        all_items.append(list_item)
                        all_header_info.append({k: v for k, v in current_heading_by_level.items()})
                    list_items = []
            
            # Process section headers
            if isinstance(item, SectionHeaderItem) or (
                isinstance(item, TextItem) and 
                item.label in [DocItemLabel.SECTION_HEADER, DocItemLabel.TITLE]
            ):
                header_level = (
                    item.level if isinstance(item, SectionHeaderItem)
                    else (0 if item.label == DocItemLabel.TITLE else 1)
                )
                current_heading_by_level[header_level] = item.text
                
                keys_to_del = [k for k in current_heading_by_level if k > header_level]
                for k in keys_to_del:
                    current_heading_by_level.pop(k, None)
                
                all_items.append(item)
                all_header_info.append({k: v for k, v in current_heading_by_level.items()})
                continue
            
            if (isinstance(item, TextItem) or 
                isinstance(item, ListItem) or 
                isinstance(item, CodeItem) or
                isinstance(item, TableItem) or
                isinstance(item, PictureItem)):
                all_items.append(item)
                all_header_info.append({k: v for k, v in current_heading_by_level.items()})
        
        # Process remaining list items
        if list_items:
            for list_item in list_items:
                all_items.append(list_item)
                all_header_info.append({k: v for k, v in current_heading_by_level.items()})
        
        # Add missing tables
        missing_tables = []
        for table in dl_doc.tables:
            table_ref = getattr(table, 'self_ref', None)
            if table_ref not in processed_refs:
                missing_tables.append(table)
        
        if missing_tables:
            for missing_table in missing_tables:
                all_items.insert(0, missing_table)
                all_header_info.insert(0, {})
        
        if not all_items:
            return
        
        chunk = DocChunk(
            text="",
            meta=DocMeta(
                doc_items=all_items,
                headings=None,
                captions=None,
                origin=dl_doc.origin,
            )
        )
        
        # Store header info as private attribute
        chunk._header_info_list = all_header_info
        
        yield chunk


class HybridChunker(BaseChunker):
    """Token-aware chunker with document structure preservation"""
    
    model_config = ConfigDict(arbitrary_types_allowed=True)
    
    tokenizer: Union[PreTrainedTokenizerBase, str] = "sentence-transformers/all-MiniLM-L6-v2"
    max_tokens: int = 2000
    merge_peers: bool = True
    delim: str = "\n\n"
    
    _tokenizer: Optional[PreTrainedTokenizerBase] = None
    _inner_chunker: Optional[HierarchicalChunker] = None
    
    @model_validator(mode="after")
    def _initialize_components(self) -> Self:
        """Initialize tokenizer and inner chunker"""
        self._tokenizer = (
            self.tokenizer
            if isinstance(self.tokenizer, PreTrainedTokenizerBase)
            else AutoTokenizer.from_pretrained(self.tokenizer)
        )
        
        if self._inner_chunker is None:
            self._inner_chunker = HierarchicalChunker()
        
        return self
    
    def _count_tokens(self, text: str) -> int:
        """Count tokens in text safely"""
        if not text:
            return 0
        
        # Split text into smaller chunks for safe tokenization
        max_chunk_length = 300
        total_tokens = 0
        
        lines = text.split('\n')
        current_chunk = ""
        
        for line in lines:
            temp_chunk = current_chunk + '\n' + line if current_chunk else line
            
            if len(temp_chunk) <= max_chunk_length:
                current_chunk = temp_chunk
            else:
                if current_chunk:
                    try:
                        total_tokens += len(self._tokenizer.tokenize(current_chunk))
                    except Exception:
                        total_tokens += int(len(current_chunk.split()) * 1.3)
                
                current_chunk = line
        
        # Process last chunk
        if current_chunk:
            try:
                total_tokens += len(self._tokenizer.tokenize(current_chunk))
            except Exception:
                total_tokens += int(len(current_chunk.split()) * 1.3)
        
        return total_tokens
    
    def _generate_text_from_items_with_headers(
        self, 
        items: List[DocItem], 
        header_info_list: List[dict],
        dl_doc: DoclingDocument
    ) -> str:
        """Generate text from items with header information"""
        text_parts = []
        current_section_headers = {}
        
        for i, item in enumerate(items):
            item_headers = header_info_list[i] if i < len(header_info_list) else {}
            
            # Add headers if changed
            if item_headers != current_section_headers:
                headers_to_add = []
                for level in sorted(item_headers.keys()):
                    if (level not in current_section_headers or 
                        current_section_headers[level] != item_headers[level]):
                        for l in sorted(item_headers.keys()):
                            if l <= level:
                                headers_to_add.append(item_headers[l])
                        break
                
                if headers_to_add:
                    header_text = "\n".join(headers_to_add)
                    text_parts.append(header_text)
                
                current_section_headers = item_headers.copy()
            
            # Add item text
            if isinstance(item, TableItem):
                table_text = self._extract_table_text(item, dl_doc)
                if table_text:
                    text_parts.append(table_text)
            elif hasattr(item, 'text') and item.text:
                is_section_header = (
                    isinstance(item, SectionHeaderItem) or 
                    (isinstance(item, TextItem) and 
                     item.label in [DocItemLabel.SECTION_HEADER])
                )
                
                if not is_section_header:
                    text_parts.append(item.text)
            elif isinstance(item, PictureItem):
                text_parts.append("")
        
        return self.delim.join(text_parts)
    
    def _extract_table_text(self, table_item: TableItem, dl_doc: DoclingDocument) -> str:
        """Extract text from table"""
        try:
            table_text = table_item.export_to_markdown(dl_doc)
            if table_text and table_text.strip():
                return table_text
        except Exception:
            pass
        
        try:
            if hasattr(table_item, 'data') and table_item.data:
                cell_texts = []
                
                if hasattr(table_item.data, 'table_cells'):
                    for cell in table_item.data.table_cells:
                        if hasattr(cell, 'text') and cell.text and cell.text.strip():
                            cell_texts.append(cell.text.strip())
                
                elif hasattr(table_item.data, 'grid') and table_item.data.grid:
                    for row in table_item.data.grid:
                        if isinstance(row, list):
                            for cell in row:
                                if hasattr(cell, 'text') and cell.text and cell.text.strip():
                                    cell_texts.append(cell.text.strip())
                
                if cell_texts:
                    return ' '.join(cell_texts)
        except Exception:
            pass
        
        if hasattr(table_item, 'text') and table_item.text:
            return table_item.text
        
        return ""
    
    def _extract_used_headers(self, header_info_list: List[dict]) -> Optional[List[str]]:
        """Extract used headers from header info list"""
        if not header_info_list:
            return None
        
        all_headers = set()
        for header_info in header_info_list:
            if header_info:
                for level, header_text in header_info.items():
                    if header_text:
                        all_headers.add(header_text)
        
        return list(all_headers) if all_headers else None
    
    def _split_document_by_tokens(self, doc_chunk: DocChunk, dl_doc: DoclingDocument) -> List[DocChunk]:
        """Split document by token limits"""
        items = doc_chunk.meta.doc_items
        header_info_list = getattr(doc_chunk, '_header_info_list', [])
        
        if not items:
            return []
        
        result_chunks = []
        current_items = []
        current_header_infos = []
        
        i = 0
        while i < len(items):
            item = items[i]
            header_info = header_info_list[i] if i < len(header_info_list) else {}
            
            # Special handling for tables
            if isinstance(item, TableItem):
                if current_items:
                    chunk_text = self._generate_text_from_items_with_headers(
                        current_items, current_header_infos, dl_doc
                    )
                    
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
                
                # Create table chunk
                table_items = [item]
                table_header_infos = [header_info]
                
                table_text = self._generate_text_from_items_with_headers(
                    table_items, table_header_infos, dl_doc
                )
                table_tokens = self._count_tokens(table_text)
                
                if table_tokens > self.max_tokens:
                    table_only_text = self._generate_text_from_items_with_headers(
                        [item], [header_info], dl_doc
                    )
                    used_headers = self._extract_used_headers([header_info])
                    result_chunks.append(DocChunk(
                        text=table_only_text,
                        meta=DocMeta(
                            doc_items=[item],
                            headings=used_headers,
                            captions=None,
                            origin=doc_chunk.meta.origin,
                        )
                    ))
                else:
                    used_headers = self._extract_used_headers(table_header_infos)
                    new_chunk = DocChunk(
                        text=table_text,
                        meta=DocMeta(
                            doc_items=table_items,
                            headings=used_headers,
                            captions=None,
                            origin=doc_chunk.meta.origin,
                        )
                    )
                    new_chunk._header_info_list = table_header_infos
                    result_chunks.append(new_chunk)
                
                i += 1
                continue
            
            # Regular item processing
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
                if current_items:
                    chunk_text = self._generate_text_from_items_with_headers(
                        current_items, current_header_infos, dl_doc
                    )
                    
                    used_headers = self._extract_used_headers(current_header_infos)
                    new_chunk = DocChunk(
                        text=chunk_text,
                        meta=DocMeta(
                            doc_items=current_items.copy(),
                            headings=used_headers,
                            captions=None,
                            origin=doc_chunk.meta.origin,
                        )
                    )
                    new_chunk._header_info_list = current_header_infos.copy()
                    result_chunks.append(new_chunk)
                    
                    current_items = [item]
                    current_header_infos = [header_info]
                else:
                    single_text = self._generate_text_from_items_with_headers(
                        [item], [header_info], dl_doc
                    )
                    
                    used_headers = self._extract_used_headers([header_info])
                    new_chunk = DocChunk(
                        text=single_text,
                        meta=DocMeta(
                            doc_items=[item],
                            headings=used_headers,
                            captions=None,
                            origin=doc_chunk.meta.origin,
                        )
                    )
                    new_chunk._header_info_list = [header_info]
                    result_chunks.append(new_chunk)
            
            i += 1
        
        # Process remaining items
        if current_items:
            chunk_text = self._generate_text_from_items_with_headers(
                current_items, current_header_infos, dl_doc
            )
            
            used_headers = self._extract_used_headers(current_header_infos)
            new_chunk = DocChunk(
                text=chunk_text,
                meta=DocMeta(
                    doc_items=current_items,
                    headings=used_headers,
                    captions=None,
                    origin=doc_chunk.meta.origin,
                )
            )
            new_chunk._header_info_list = current_header_infos
            result_chunks.append(new_chunk)
        
        return self._merge_small_chunks(result_chunks, dl_doc)
    
    def _merge_small_chunks(self, chunks: List[DocChunk], dl_doc: DoclingDocument) -> List[DocChunk]:
        """Merge small chunks for better token efficiency"""
        if not chunks:
            return chunks
        
        min_chunk_size = self.max_tokens // 3
        merged_chunks = []
        current_merge_candidate = None
        
        for chunk in chunks:
            chunk_tokens = self._count_tokens(chunk.text)
            
            if chunk_tokens > self.max_tokens:
                if current_merge_candidate:
                    merged_chunks.append(current_merge_candidate)
                    current_merge_candidate = None
                merged_chunks.append(chunk)
                continue
            
            if chunk_tokens < min_chunk_size:
                if current_merge_candidate is None:
                    current_merge_candidate = chunk
                else:
                    # Try merging
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
                        new_chunk = DocChunk(
                            text=merged_text,
                            meta=DocMeta(
                                doc_items=merged_items,
                                headings=self._extract_used_headers(merged_header_infos),
                                captions=None,
                                origin=chunk.meta.origin,
                            )
                        )
                        new_chunk._header_info_list = merged_header_infos
                        current_merge_candidate = new_chunk
                    else:
                        merged_chunks.append(current_merge_candidate)
                        current_merge_candidate = chunk
            else:
                if current_merge_candidate:
                    candidate_tokens = self._count_tokens(current_merge_candidate.text)
                    if candidate_tokens < min_chunk_size:
                        # Try merging with current chunk
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
                            new_chunk = DocChunk(
                                text=merged_text,
                                meta=DocMeta(
                                    doc_items=merged_items,
                                    headings=self._extract_used_headers(merged_header_infos),
                                    captions=None,
                                    origin=chunk.meta.origin,
                                )
                            )
                            new_chunk._header_info_list = merged_header_infos
                            merged_chunks.append(new_chunk)
                            current_merge_candidate = None
                            continue
                    
                    merged_chunks.append(current_merge_candidate)
                    current_merge_candidate = None
                
                merged_chunks.append(chunk)
        
        if current_merge_candidate:
            merged_chunks.append(current_merge_candidate)
        
        return merged_chunks
    
    def chunk(self, dl_doc: DoclingDocument, **kwargs: Any) -> Iterator[BaseChunk]:
        """Chunk the document with token awareness
        
        Args:
            dl_doc: Document to chunk
            
        Yields:
            Token-limited chunks
        """
        doc_chunks = list(self._inner_chunker.chunk(dl_doc=dl_doc, **kwargs))
        
        if not doc_chunks:
            return iter([])
        
        doc_chunk = doc_chunks[0]
        final_chunks = self._split_document_by_tokens(doc_chunk, dl_doc)
        
        return iter(final_chunks)