"""Simple document processor using pypdf"""
import logging
from pathlib import Path
from typing import List, Dict, Any
from pydantic import BaseModel

logger = logging.getLogger(__name__)

class ProcessedChunk(BaseModel):
    text: str
    title: str = ""
    created_date: int = 0
    i_page: int = 0
    e_page: int = 0
    chunk_bboxes: str = "[]"
    media_files: str = "[]"

class SimpleDocumentProcessor:
    """Simple processor using pypdf without complex dependencies"""
    
    def __init__(self):
        self.name = "simple"
        logger.info("SimpleDocumentProcessor initialized")
    
    async def __call__(self, request, file_path: str, **params) -> List[ProcessedChunk]:
        """Process a document file"""
        try:
            from pypdf import PdfReader
            
            file_path_obj = Path(file_path)
            if not file_path_obj.exists():
                raise FileNotFoundError(f"File not found: {file_path}")
            
            # Extract text from PDF
            reader = PdfReader(file_path)
            pages_text = []
            
            for page_num, page in enumerate(reader.pages, 1):
                try:
                    text = page.extract_text()
                    if text:
                        pages_text.append({
                            'page': page_num,
                            'text': text.strip()
                        })
                except Exception as e:
                    logger.warning(f"Error extracting text from page {page_num}: {e}")
            
            # Create chunks from extracted text
            chunks = []
            for page_info in pages_text:
                chunk = ProcessedChunk(
                    text=page_info['text'],
                    title=file_path_obj.stem,
                    created_date=0,
                    i_page=page_info['page'],
                    e_page=page_info['page'],
                    chunk_bboxes="[]",
                    media_files="[]"
                )
                chunks.append(chunk)
            
            logger.info(f"Processed {len(chunks)} chunks from {file_path}")
            return chunks
            
        except Exception as e:
            logger.error(f"Error processing file: {e}")
            raise
