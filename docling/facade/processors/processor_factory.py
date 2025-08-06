"""Processor Factory for dynamic processor selection"""

from typing import Dict, Any, Optional
from docling.facade.utils.base import BaseProcessor
from docling.facade.config import ProcessorMode
from docling.facade.processors.docling_processor import DoclingProcessor
from docling.facade.processors.audio_processor import AudioProcessor
from docling.facade.processors.tabular_processor import TabularProcessor
from docling.facade.processors.langchain_processor import LangChainProcessor


class IntelligentLangChainProcessor(LangChainProcessor):
    """
    지능형 LangChain Processor
    Future: Docling으로 확장 가능한 문서들을 위한 플레이스홀더
    """
    
    def __init__(self):
        super().__init__()
        self.mode = "intelligent"
    
    async def process(self, file_path: str, request: Any = None, **kwargs) -> list:
        """
        지능형 처리 - 향후 Docling 확장 가능
        현재는 LangChain과 동일하지만, 추가 기능 구현 가능
        """
        # TODO: Add intelligent features like:
        # - Better chunking strategies
        # - Semantic understanding
        # - Advanced metadata extraction
        return await super().process(file_path, request, **kwargs)


class ProcessorFactory:
    """
    Factory class for creating appropriate processors
    모드와 확장자에 따라 적절한 프로세서 생성
    """
    
    # Processor registry
    PROCESSORS = {
        'docling': DoclingProcessor,
        'audio': AudioProcessor,
        'tabular': TabularProcessor,
        'langchain': LangChainProcessor,
        'intelligent_langchain': IntelligentLangChainProcessor,
    }
    
    # Extension to processor mapping (for intelligent mode)
    INTELLIGENT_MAPPING = {
        '.pdf': 'docling',
        '.hwpx': 'docling',
        '.docx': 'intelligent_langchain',  # Future: can be upgraded to docling
        '.pptx': 'intelligent_langchain',  # Future: can be upgraded to docling
        '.doc': 'intelligent_langchain',
        '.ppt': 'intelligent_langchain',
        '.hwp': 'intelligent_langchain',
        '.txt': 'intelligent_langchain',
        '.json': 'intelligent_langchain',
        '.md': 'intelligent_langchain',
        '.csv': 'tabular',  # Tabular stays same
        '.xlsx': 'tabular',  # Tabular stays same
        '.mp3': 'audio',  # Audio stays same
        '.m4a': 'audio',
        '.wav': 'audio',
    }
    
    # Extension to processor mapping (for basic mode)
    BASIC_MAPPING = {
        '.pdf': 'langchain',  # Use LangChain instead of Docling
        '.hwpx': 'langchain',  # Use LangChain instead of Docling
        '.docx': 'langchain',
        '.doc': 'langchain',
        '.pptx': 'langchain',
        '.ppt': 'langchain',
        '.hwp': 'langchain',
        '.txt': 'langchain',
        '.json': 'langchain',
        '.md': 'langchain',
        '.csv': 'tabular',
        '.xlsx': 'tabular',
        '.mp3': 'audio',
        '.m4a': 'audio',
        '.wav': 'audio',
    }
    
    def __init__(self):
        """Initialize processor factory"""
        self._processor_cache = {}
    
    def create_processor(self, extension: str, mode: ProcessorMode, 
                        options: Optional[Dict[str, Any]] = None,
                        config: Optional[Dict[str, Any]] = None) -> BaseProcessor:
        """
        Create appropriate processor based on extension and mode
        
        Args:
            extension: File extension
            mode: ProcessorMode.BASIC or ProcessorMode.INTELLIGENT
            options: Processor-specific options
            config: Global configuration (backwards compatibility)
        
        Returns:
            Appropriate processor instance
        """
        if not extension.startswith('.'):
            extension = '.' + extension
        
        # Select mapping based on mode
        if mode == ProcessorMode.INTELLIGENT:
            mapping = self.INTELLIGENT_MAPPING
        else:
            mapping = self.BASIC_MAPPING
        
        # Get processor name
        processor_name = mapping.get(extension, 'langchain')
        
        # Create cache key including options hash
        import json
        import hashlib
        options_str = json.dumps(options or {}, sort_keys=True)
        options_hash = hashlib.md5(options_str.encode()).hexdigest()[:8]
        cache_key = f"{processor_name}_{mode.value}_{options_hash}"
        
        # Check cache
        if cache_key in self._processor_cache:
            return self._processor_cache[cache_key]
        
        # Create new processor
        processor_class = self.PROCESSORS.get(processor_name)
        if not processor_class:
            raise ValueError(f"Unknown processor: {processor_name}")
        
        # Create processor with options
        if processor_name == 'audio':
            # Audio processor needs special handling for backwards compatibility
            whisper_url = None
            if config and 'whisper_url' in config:
                whisper_url = config['whisper_url']
            processor = processor_class(options=options, whisper_url=whisper_url)
        elif processor_name in ['docling', 'langchain', 'intelligent_langchain']:
            processor = processor_class(options=options)
        else:
            # Tabular and others don't need options yet
            processor = processor_class()
        
        # Cache the processor
        self._processor_cache[cache_key] = processor
        
        return processor
    
    def get_processor_info(self, extension: str, mode: ProcessorMode) -> Dict[str, Any]:
        """
        Get information about which processor will be used
        
        Args:
            extension: File extension
            mode: Processing mode
        
        Returns:
            Dictionary with processor information
        """
        if not extension.startswith('.'):
            extension = '.' + extension
        
        # Select mapping based on mode
        if mode == ProcessorMode.INTELLIGENT:
            mapping = self.INTELLIGENT_MAPPING
            mode_str = "Intelligent (지능형)"
        else:
            mapping = self.BASIC_MAPPING
            mode_str = "Basic (기본형)"
        
        processor_name = mapping.get(extension, 'langchain')
        
        # Get detailed description
        descriptions = {
            'docling': 'Docling with enrichment (고급 PDF/HWPX 처리)',
            'audio': 'Audio transcription using Whisper (음성 전사)',
            'tabular': 'Tabular data processing (테이블 데이터)',
            'langchain': 'LangChain document processing (기본 문서 처리)',
            'intelligent_langchain': 'Enhanced LangChain processing (향상된 문서 처리)'
        }
        
        return {
            'extension': extension,
            'mode': mode_str,
            'processor': processor_name,
            'description': descriptions.get(processor_name, 'Unknown processor'),
            'has_enrichment': processor_name == 'docling'
        }
    
    def clear_cache(self):
        """Clear processor cache"""
        self._processor_cache.clear()