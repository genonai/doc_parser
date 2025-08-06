"""Tabular Processor for CSV and XLSX files"""

import os
import pandas as pd
from datetime import datetime
from typing import List, Dict, Any

from docling.facade.models import GenOSVectorMeta
from docling.facade.utils.base import BaseProcessor


class TabularLoader:
    """서부발전전처리기.py의 TabularLoader 클래스"""
    
    def __init__(self, file_path: str, file_type: str):
        self.file_path = file_path
        self.file_type = file_type
    
    def return_vectormeta_format(self):
        ext = self.file_type.lower()
        
        try:
            if ext == '.csv':
                df = pd.read_csv(self.file_path)
            elif ext == '.xlsx':
                df = pd.read_excel(self.file_path)
            else:
                return []
            
            # Convert dataframe to text format
            text = df.to_string()
            
            res = [GenOSVectorMeta.model_validate({
                'text': text,
                'n_char': len(text),
                'n_word': len(text.split()),
                'n_line': len(text.splitlines()),
                'i_page': 0,
                'e_page': 0,
                'i_chunk_on_page': 0,
                'n_chunk_of_page': 1,
                'i_chunk_on_doc': 0,
                'n_chunk_of_doc': 1,
                'n_page': 1,
                'reg_date': datetime.now().isoformat(timespec='seconds') + 'Z',
                'chunk_bboxes': "[]",
                'media_files': "[]"
            })]
            return res
        except Exception as e:
            print(f"Error processing tabular file: {e}")
            # Return default empty vector
            return [GenOSVectorMeta.model_validate({
                'text': "",
                'n_char': 0,
                'n_word': 0,
                'n_line': 0,
                'i_page': 0,
                'e_page': 0,
                'i_chunk_on_page': 0,
                'n_chunk_of_page': 1,
                'i_chunk_on_doc': 0,
                'n_chunk_of_doc': 1,
                'n_page': 1,
                'reg_date': datetime.now().isoformat(timespec='seconds') + 'Z',
                'chunk_bboxes': "[]",
                'media_files': "[]"
            })]


class TabularProcessor(BaseProcessor):
    """Process tabular files (CSV, XLSX)"""
    
    async def process(self, file_path: str, request: Any = None, **kwargs) -> List[Dict]:
        """Process tabular file"""
        
        ext = os.path.splitext(file_path)[-1].lower()
        loader = TabularLoader(file_path, ext)
        vector_objs = loader.return_vectormeta_format()
        
        # Convert objects to dict list
        vectors = [v.model_dump() for v in vector_objs] if vector_objs else []
        return vectors
    
    def supports(self, file_path: str) -> bool:
        """Check if this processor supports the file"""
        ext = os.path.splitext(file_path)[-1].lower()
        return ext in ('.csv', '.xlsx')