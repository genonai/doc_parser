"""Enhanced Document Facade with configurable processor selection"""

import os
from typing import List, Dict, Any, Optional
from docling.facade.config import ProcessorConfig, ProcessorMode
from docling.facade.processors.processor_factory import ProcessorFactory


class DocumentFacade:
    """
    Enhanced Facade with configurable processor selection
    각 확장자별로 지능형/기본형 처리 선택 가능
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None, 
                 config_file: Optional[str] = None):
        """
        Initialize enhanced document facade
        
        Args:
            config: Optional configuration dictionary
            config_file: Optional path to JSON configuration file
        """
        # Default configuration
        default_config = {
            'whisper_url': "http://192.168.74.164:30100/v1/audio/transcriptions",
            'chunk_size': 1000,
            'chunk_overlap': 200,
            'max_tokens': 1024,
        }
        
        # Merge configurations
        self.config = {**default_config, **(config or {})}
        
        # Initialize processor configuration
        self.processor_config = ProcessorConfig(config_file)
        
        # Initialize processor factory
        self.processor_factory = ProcessorFactory()
    
    def set_mode(self, extension: str, mode: ProcessorMode):
        """
        Set processing mode for specific extension
        
        Args:
            extension: File extension (e.g., '.pdf')
            mode: ProcessorMode.BASIC or ProcessorMode.INTELLIGENT
        """
        self.processor_config.set_mode(extension, mode)
        self.processor_factory.clear_cache()  # Clear cache to reload processors
    
    def toggle_mode(self, extension: str) -> ProcessorMode:
        """
        Toggle processing mode for extension
        
        Args:
            extension: File extension
            
        Returns:
            New mode after toggling
        """
        new_mode = self.processor_config.toggle_mode(extension)
        self.processor_factory.clear_cache()
        return new_mode
    
    def set_all_intelligent(self):
        """Set all extensions to intelligent mode"""
        self.processor_config.set_all_intelligent()
        self.processor_factory.clear_cache()
    
    def set_all_basic(self):
        """Set all extensions to basic mode"""
        self.processor_config.set_all_basic()
        self.processor_factory.clear_cache()
    
    def save_config(self, config_file: Optional[str] = None):
        """Save current configuration to file"""
        self.processor_config.save_config(config_file)
    
    def load_config(self, config_file: str):
        """Load configuration from file"""
        self.processor_config.load_config(config_file)
        self.processor_factory.clear_cache()
    
    def print_status(self):
        """Print current configuration status"""
        self.processor_config.print_status()
    
    async def process(self, file_path: str, request: Any = None, **kwargs) -> List[Dict]:
        """
        Process a document file with configured processor
        
        Args:
            file_path: Path to the file to process
            request: Optional request object for async operations
            **kwargs: Additional processing parameters
            
        Returns:
            List of vector dictionaries ready for storage
        """
        # Validate file exists
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")
        
        # Get extension
        ext = os.path.splitext(file_path)[-1].lower()
        
        # Get processing mode and options
        mode = self.processor_config.get_mode(ext)
        processor_options = self.processor_config.get_processor_options(ext)
        
        # Create appropriate processor with options
        processor = self.processor_factory.create_processor(
            ext, mode, 
            options=processor_options,
            config=self.config
        )
        
        # Merge all parameters
        processing_params = {
            **self.config,
            **processor_options,
            **kwargs
        }
        
        # Process the file
        vectors = await processor.process(file_path, request, **processing_params)
        
        return vectors
    
    def get_processor_info(self, file_path: str) -> Dict[str, Any]:
        """
        Get information about which processor will be used
        
        Args:
            file_path: Path to the file
            
        Returns:
            Dictionary with processor information
        """
        ext = os.path.splitext(file_path)[-1].lower()
        mode = self.processor_config.get_mode(ext)
        options = self.processor_config.get_processor_options(ext)
        
        info = self.processor_factory.get_processor_info(ext, mode)
        info['options'] = options
        return info
    
    def get_supported_extensions(self) -> List[str]:
        """Get list of supported file extensions"""
        return list(self.processor_config.config.keys())
    
    def get_configuration(self) -> Dict[str, Dict[str, Any]]:
        """Get current configuration for all extensions"""
        return self.processor_config.get_status()
    
    def set_processor_option(self, extension: str, option_path: str, value: Any):
        """
        Set processor option for specific extension
        
        Args:
            extension: File extension
            option_path: Dot-separated path to option
            value: New value
        """
        self.processor_config.set_processor_option(extension, option_path, value)
        self.processor_factory.clear_cache()
    
    def set_enrichment_options(self, extension: str, **options):
        """
        Set enrichment options for extension
        
        Args:
            extension: File extension
            **options: Enrichment options (enabled, toc_extraction_mode, etc.)
        """
        for key, value in options.items():
            self.set_processor_option(extension, f'enrichment.{key}', value)
    
    def set_pipeline_options(self, extension: str, **options):
        """
        Set pipeline options for extension
        
        Args:
            extension: File extension
            **options: Pipeline options (do_ocr, do_table_structure, etc.)
        """
        for key, value in options.items():
            self.set_processor_option(extension, f'pipeline.{key}', value)
    
    def set_chunking_options(self, extension: str, **options):
        """
        Set chunking options for extension
        
        Args:
            extension: File extension
            **options: Chunking options (max_tokens, merge_peers, etc.)
        """
        for key, value in options.items():
            self.set_processor_option(extension, f'chunking.{key}', value)