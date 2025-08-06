"""LangChain Processor for general documents"""

import os
import sys
import json
import subprocess
import fitz  # PyMuPDF
from datetime import datetime
from typing import List, Dict, Any
from collections import defaultdict
from markdown import markdown
from weasyprint import HTML

from langchain_community.document_loaders import (
    PyMuPDFLoader,
    UnstructuredWordDocumentLoader,
    UnstructuredPowerPointLoader,
    UnstructuredImageLoader,
    TextLoader,
    UnstructuredMarkdownLoader,
    UnstructuredFileLoader
)
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.schema import Document

from docling.facade.models import GenOSVectorMeta, GenOSVectorMetaBuilder
from docling.facade.utils.base import BaseProcessor


class HwpLoader:
    """HWP 파일 로더"""
    
    def __init__(self, file_path):
        self.file_path = file_path
    
    def load(self):
        try:
            # HWP 파일을 PDF로 변환 후 로드
            pdf_path = self.file_path.replace('.hwp', '.pdf')
            subprocess.run(['hwp2pdf', self.file_path, pdf_path], check=True)
            loader = PyMuPDFLoader(pdf_path)
            return loader.load()
        except:
            # 변환 실패 시 UnstructuredFileLoader 사용
            loader = UnstructuredFileLoader(self.file_path)
            return loader.load()


class LangChainProcessor(BaseProcessor):
    """Process documents using LangChain loaders"""
    
    def __init__(self, options: Dict[str, Any] = None):
        super().__init__()
        self.options = options or {}
        self.page_chunk_counts = defaultdict(int)
    
    def get_loader(self, file_path: str):
        """Get appropriate loader based on file extension"""
        ext = os.path.splitext(file_path)[-1].lower()
        
        if ext == '.pdf':
            return PyMuPDFLoader(file_path)
        elif ext in ['.doc', '.docx']:
            return UnstructuredWordDocumentLoader(file_path)
        elif ext in ['.ppt', '.pptx']:
            return UnstructuredPowerPointLoader(file_path)
        elif ext in ['.jpg', '.jpeg', '.png']:
            return UnstructuredImageLoader(file_path)
        elif ext in ['.txt', '.json']:
            return TextLoader(file_path)
        elif ext == '.md':
            return UnstructuredMarkdownLoader(file_path)
        elif ext == '.hwp':
            return HwpLoader(file_path)
        else:
            return UnstructuredFileLoader(file_path)
    
    def convert_to_pdf(self, file_path: str):
        """Convert PPT to PDF"""
        out_path = "."
        try:
            subprocess.run(['soffice', '--headless', '--convert-to', 'pdf', '--outdir', out_path, file_path], check=True)
            pdf_path = os.path.basename(file_path).replace(file_path.split('.')[-1], 'pdf')
            return pdf_path
        except subprocess.CalledProcessError as e:
            print(f"Error converting to PDF: {e}")
            return None
    
    def convert_md_to_pdf(self, md_path):
        """Convert Markdown to PDF"""
        try:
            import chardet
            
            pdf_path = md_path.replace('.md', '.pdf')
            with open(md_path, 'rb') as f:
                raw_file = f.read(100)
            enc_type = chardet.detect(raw_file)['encoding']
            with open(md_path, 'r', encoding=enc_type) as f:
                md_content = f.read()
            
            html_content = markdown(md_content)
            HTML(string=html_content).write_pdf(pdf_path)
            return pdf_path
        except:
            return None
    
    def load_documents(self, file_path: str, **kwargs) -> List[Document]:
        """Load documents using appropriate loader"""
        loader = self.get_loader(file_path)
        documents = loader.load()
        return documents
    
    def split_documents(self, documents: List[Document], **kwargs) -> List[Document]:
        """Split documents into chunks"""
        # Merge options
        splitter_config = self.options.get('text_splitter', {})
        splitter_params = {
            'chunk_size': splitter_config.get('chunk_size', 1000),
            'chunk_overlap': splitter_config.get('chunk_overlap', 200),
            **kwargs  # Allow override from kwargs
        }
        
        text_splitter = RecursiveCharacterTextSplitter(**splitter_params)
        chunks = text_splitter.split_documents(documents)
        chunks = [chunk for chunk in chunks if chunk.page_content]
        
        if not chunks:
            raise Exception('Empty document')
        
        # Count chunks per page
        for chunk in chunks:
            page = chunk.metadata.get('page', 0)
            self.page_chunk_counts[page] += 1
        
        return chunks
    
    def compose_vectors(self, file_path: str, chunks: List[Document], **kwargs) -> List[GenOSVectorMeta]:
        """Compose vectors from chunks"""
        
        # Handle special file types
        if file_path.endswith('.md'):
            pdf_path = self.convert_md_to_pdf(file_path)
        elif file_path.endswith('.ppt'):
            pdf_path = self.convert_to_pdf(file_path)
            if not pdf_path:
                pdf_path = None
        else:
            pdf_path = file_path.replace('.hwp', '.pdf').replace('.txt', '.pdf').replace('.json', '.pdf')
        
        doc = fitz.open(pdf_path) if pdf_path and os.path.exists(pdf_path) else None
        
        # Handle PPT specific logic
        if file_path.endswith('.ppt'):
            try:
                subprocess.run(['rm', pdf_path], check=True)
            except:
                pass
        
        # Create global metadata
        global_metadata = {
            'n_chunk_of_doc': len(chunks),
            'n_page': len(self.page_chunk_counts) if self.page_chunk_counts else 1,
            'reg_date': datetime.now().isoformat(timespec='seconds') + 'Z',
        }
        
        vectors = []
        current_page = None
        chunk_index_on_page = 0
        
        for i, chunk in enumerate(chunks):
            page = chunk.metadata.get('page', 0)
            
            if page != current_page:
                current_page = page
                chunk_index_on_page = 0
            
            # Extract bounding boxes if available
            bbox = None
            if doc and page < len(doc):
                doc_page = doc[page]
                text_instances = doc_page.search_for(chunk.page_content[:50])
                if text_instances:
                    rect = text_instances[0]
                    bbox = json.dumps([{
                        'page': page,
                        'bbox': {
                            'l': rect.x0 / doc_page.rect.width,
                            't': rect.y0 / doc_page.rect.height,
                            'r': rect.x1 / doc_page.rect.width,
                            'b': rect.y1 / doc_page.rect.height,
                            'coord_origin': 'TOPLEFT'
                        },
                        'type': 'text',
                        'ref': f'text_{i}'
                    }])
            
            # Create vector
            vector = GenOSVectorMeta.model_validate({
                'text': chunk.page_content,
                'n_char': len(chunk.page_content),
                'n_word': len(chunk.page_content.split()),
                'n_line': len(chunk.page_content.splitlines()),
                'i_page': page,
                'e_page': page,
                'i_chunk_on_page': chunk_index_on_page,
                'n_chunk_of_page': self.page_chunk_counts.get(page, 1),
                'i_chunk_on_doc': i,
                'chunk_bboxes': bbox or "[]",
                'media_files': "[]",
                **global_metadata
            })
            vectors.append(vector)
            chunk_index_on_page += 1
        
        return vectors
    
    async def process(self, file_path: str, request: Any = None, **kwargs) -> List[Dict]:
        """Process document using LangChain"""
        
        # Reset page chunk counts
        self.page_chunk_counts = defaultdict(int)
        
        # Load and split documents
        documents = self.load_documents(file_path, **kwargs)
        chunks = self.split_documents(documents, **kwargs)
        
        # Compose vectors
        vector_objs = self.compose_vectors(file_path, chunks, **kwargs)
        
        # Convert to dict list
        vectors = [v.model_dump() for v in vector_objs]
        return vectors
    
    def supports(self, file_path: str) -> bool:
        """Check if this processor supports the file"""
        ext = os.path.splitext(file_path)[-1].lower()
        return ext in ('.doc', '.docx', '.ppt', '.pptx', '.hwp', '.txt', '.json', '.md')