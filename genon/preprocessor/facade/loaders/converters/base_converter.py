from abc import ABC, abstractmethod


class BaseFormatConverter(ABC):
    @abstractmethod
    def convert(self, src: str) -> str:
        """파일을 PDF로 변환하고 pdf_path를 반환"""
        ...
