"""Docling Processor for PDF and HWPX files with enrichment"""

import os
import asyncio
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any
from collections import defaultdict

from docling.document_converter import DocumentConverter, PdfFormatOption, HwpxFormatOption
from docling.datamodel.pipeline_options import DataEnrichmentOptions
from docling.utils.document_enrichment import enrich_document
from docling.datamodel.document import ConversionResult
from docling_core.types import DoclingDocument
from docling_core.types.doc import PictureItem

from docling.facade.models import GenOSVectorMetaBuilder
from docling.facade.utils.base import BaseProcessor
from docling.facade.chunkers import HybridChunker


class DoclingProcessor(BaseProcessor):
    """Process PDF and HWPX files using Docling with enrichment"""
    
    def __init__(self, options: Dict[str, Any] = None):
        super().__init__()
        self.options = options or {}
        
        # Get pipeline options
        pipeline_opts = self.options.get('pipeline', {})
        
        # Initialize Docling converter with dynamic options
        pdf_options = PdfFormatOption(
            do_ocr=pipeline_opts.get('do_ocr', False),
            do_table_structure=pipeline_opts.get('do_table_structure', True),
            table_structure_options=pipeline_opts.get('table_structure_options', {
                "do_cell_matching": True
            })
        )
        
        self.converter = DocumentConverter(
            allowed_formats=[pdf_options, HwpxFormatOption()]
        )
        self.page_chunk_counts = defaultdict(int)
    
    async def process(self, file_path: str, request: Any = None, **kwargs) -> List[Dict]:
        """Process PDF or HWPX file with enrichment"""
        
        # Merge provided options with instance options
        processing_options = {**self.options, **kwargs}
        
        # 1. Document conversion
        conv_result: ConversionResult = self.converter.convert(file_path, raises_on_error=True)
        document = conv_result.document
        
        # 2. Apply enrichment (if enabled)
        enrichment_config = processing_options.get('enrichment', {})
        if enrichment_config.get('enabled', True):
            enrichment_options = DataEnrichmentOptions(
                toc_extraction_mode=enrichment_config.get('toc_extraction_mode', 'list_items'),
                toc_seed=enrichment_config.get('toc_seed', 33),
                toc_max_tokens=enrichment_config.get('toc_max_tokens', 1000)
            )
            document = enrich_document(document, enrichment_options)
        
        # 3. Handle images
        output_path, output_file = os.path.split(file_path)
        filename, _ = os.path.splitext(output_file)
        artifacts_dir = Path(f"{output_path}/{filename}")
        if artifacts_dir.is_absolute():
            reference_path = None
        else:
            reference_path = artifacts_dir.parent
        
        document = document._with_pictures_refs(image_dir=artifacts_dir, reference_path=reference_path)
        
        # 4. Chunking
        chunking_config = processing_options.get('chunking', {})
        chunker = HybridChunker(
            max_tokens=chunking_config.get('max_tokens', 1024),
            merge_peers=chunking_config.get('merge_peers', True)
        )
        chunks = list(chunker.chunk(dl_doc=document, **processing_options))
        
        # Count chunks per page
        for chunk in chunks:
            self.page_chunk_counts[chunk.meta.doc_items[0].prov[0].page_no] += 1
        
        # 5. Create vectors
        global_metadata = dict(
            n_chunk_of_doc=len(chunks) if chunks else 1,
            n_page=document.num_pages() if document.num_pages() > 0 else 1,
            reg_date=datetime.now().isoformat(timespec='seconds') + 'Z',
        )
        
        current_page = None
        chunk_index_on_page = 0
        vectors = []
        upload_tasks = []
        
        for chunk_idx, chunk in enumerate(chunks):
            chunk_page = chunk.meta.doc_items[0].prov[0].page_no
            
            # Generate text with headers
            content = chunk.text
            if hasattr(chunk, '_header_info_list'):
                headers = []
                for level in [1, 2, 3]:
                    for header_info in chunk._header_info_list:
                        if level in header_info:
                            headers.append(header_info[level])
                if headers:
                    content = '\n'.join(headers) + '\n' + content
            
            if chunk_page != current_page:
                current_page = chunk_page
                chunk_index_on_page = 0
            
            vector = (GenOSVectorMetaBuilder()
                      .set_text(content)
                      .set_page_info(chunk_page, chunk_index_on_page, self.page_chunk_counts[chunk_page])
                      .set_chunk_index(chunk_idx)
                      .set_global_metadata(**global_metadata)
                      .set_chunk_bboxes(chunk.meta.doc_items, document)
                      .set_media_files(chunk.meta.doc_items)
                      ).build()
            vectors.append(vector.model_dump())
            
            chunk_index_on_page += 1
            
            # Handle media files upload
            if request:
                media_files = []
                for item in chunk.meta.doc_items:
                    if isinstance(item, PictureItem):
                        path = str(item.image.uri)
                        name = path.rsplit("/", 1)[-1]
                        media_files.append({'path': path, 'name': name})
                
                if media_files:
                    # Try to import upload_files if available
                    try:
                        import sys
                        import os
                        parent_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
                        if parent_dir not in sys.path:
                            sys.path.append(parent_dir)
                        from genos_di.genos_utils import upload_files
                        upload_tasks.append(asyncio.create_task(
                            upload_files(media_files, request=request)
                        ))
                    except ImportError:
                        # If genos_utils is not available, skip upload
                        pass
        
        if upload_tasks:
            await asyncio.gather(*upload_tasks)
        
        return vectors
    
    def supports(self, file_path: str) -> bool:
        """Check if this processor supports the file"""
        ext = os.path.splitext(file_path)[-1].lower()
        return ext in ('.pdf', '.hwpx')