"""Docling package for document processing"""

from .facade import DocumentFacade
from .facade.config import ProcessorConfig, ProcessorMode

__version__ = "1.0.0"
__all__ = ['DocumentFacade', 'ProcessorConfig', 'ProcessorMode']