"""Base processor class"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any


class BaseProcessor(ABC):
    """Abstract base class for document processors"""
    
    @abstractmethod
    async def process(self, file_path: str, request: Any = None, **kwargs) -> List[Dict]:
        """Process a file and return vectors"""
        pass
    
    @abstractmethod
    def supports(self, file_path: str) -> bool:
        """Check if this processor supports the given file"""
        pass