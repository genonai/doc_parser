import subprocess
import os
from pathlib import Path
from typing import Union
from io import BytesIO

import docling.backend.xml.hwpx_backend as hwpx_backend
from docling.backend.abstract_backend import DeclarativeDocumentBackend
from docling.datamodel.base_models import InputFormat
from docling.datamodel.document import InputDocument
from docling_core.types.doc import DoclingDocument


class HwpDocumentBackend(DeclarativeDocumentBackend):
    def __init__(self, in_doc: InputDocument, path_or_stream: Union[Path, BytesIO]) -> None:
        """Initialize the HWP backend by converting HWP to HWPX first."""
        super().__init__(in_doc, path_or_stream)
        self.hwpx_backend = None
        self.valid = False
        # HWP 파일인지 확인
        if str(path_or_stream).lower().endswith('.hwp'):
            try:
                # HWP를 HWPX로 변환
                hwpx_path = self._convert_hwp_to_hwpx(path_or_stream)
                
                # HwpxDocumentBackend 인스턴스 생성
                self.hwpx_backend = hwpx_backend.HwpxDocumentBackend(in_doc, hwpx_path)
                self.valid = self.hwpx_backend.is_valid()
            except Exception as e:
                self.valid = False
                raise RuntimeError(f"Failed to process HWP file: {e}")
        else:
            raise RuntimeError(path_or_stream)#"HwpDocumentBackend only supports .hwp files")

    def _convert_hwp_to_hwpx(self, hwp_path: Path) -> Path:
        """Convert HWP file to HWPX using hwp2hwpx.sh script."""
        try:
            # 입력 파일과 같은 디렉토리에 출력 파일 생성
            input_hwp = str(hwp_path)
            output_hwpx = str(hwp_path.with_suffix('.hwpx'))
            
            # hwp2hwpx 스크립트 실행
            result = subprocess.run([
                "/app/hwp2hwpx/run_hwp2hwpx.sh",
                input_hwp, 
                output_hwpx
            ], capture_output=True, text=True, cwd=str(hwp_path.parent))
            
            if result.returncode != 0:
                raise RuntimeError(f"HWP to HWPX conversion failed: {result.stderr}")
            
            if not os.path.exists(output_hwpx):
                raise RuntimeError(f"HWPX file was not created: {output_hwpx}")
            
            return Path(output_hwpx)
            
        except Exception as e:
            raise RuntimeError(f"Failed to convert HWP to HWPX: {e}")

    def is_valid(self) -> bool:
        return self.valid and self.hwpx_backend is not None

    @classmethod
    def supported_formats(cls) -> set:
        return set()  # HWP는 확장자로만 식별
    
    @classmethod
    def supports_pagination(cls) -> bool:
        return False
    
    def unload(self) -> None:
        # HwpxDocumentBackend 정리
        if self.hwpx_backend:
            self.hwpx_backend.unload()
            self.hwpx_backend = None

    def convert(self) -> DoclingDocument:
        """Convert HWP file to DoclingDocument by delegating to HwpxDocumentBackend."""
        if not self.is_valid():
            raise RuntimeError("Invalid HWP document or conversion failed")
        
        # HwpxDocumentBackend에 실제 변환 작업 위임
        return self.hwpx_backend.convert()