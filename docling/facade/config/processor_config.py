"""Processor configuration system for intelligent routing"""

from enum import Enum
from typing import Dict, Any, Optional
import json
import os


class ProcessorMode(Enum):
    """Processing modes for documents"""
    BASIC = "basic"          # 기본형 - 서부발전전처리기 방식
    INTELLIGENT = "intelligent"  # 지능형 - preprocess.py 방식


class ProcessorConfig:
    """
    Configuration manager for processor selection
    각 확장자별로 처리 방식을 설정 관리
    """
    
    # Default configuration - 기본값 설정
    DEFAULT_CONFIG = {
        # PDF: 지능형을 기본으로
        '.pdf': {
            'mode': ProcessorMode.INTELLIGENT,
            'processor': 'docling',  # Docling with enrichment
            'description': 'PDF with Docling + Enrichment',
            'options': {
                'enrichment': {
                    'enabled': True,
                    'toc_extraction_mode': 'list_items',
                    'toc_seed': 33,
                    'toc_max_tokens': 1000
                },
                'pipeline': {
                    'do_ocr': False,
                    'do_table_structure': True,
                    'table_structure_options': {
                        'do_cell_matching': True
                    }
                },
                'chunking': {
                    'max_tokens': 1024,
                    'merge_peers': True
                }
            }
        },
        '.hwpx': {
            'mode': ProcessorMode.INTELLIGENT,
            'processor': 'docling',
            'description': 'HWPX with Docling + Enrichment',
            'options': {
                'enrichment': {
                    'enabled': True,
                    'toc_extraction_mode': 'list_items',
                    'toc_seed': 33,
                    'toc_max_tokens': 1000
                },
                'pipeline': {},
                'chunking': {
                    'max_tokens': 1024,
                    'merge_peers': True
                }
            }
        },
        
        # 오디오: 기본형
        '.mp3': {
            'mode': ProcessorMode.BASIC,
            'processor': 'audio',
            'description': 'Audio transcription with Whisper',
            'options': {
                'whisper': {
                    'model': 'model',
                    'language': 'ko',
                    'temperature': 0,
                    'chunk_sec': 29
                }
            }
        },
        '.m4a': {
            'mode': ProcessorMode.BASIC,
            'processor': 'audio',
            'description': 'Audio transcription with Whisper',
            'options': {
                'whisper': {
                    'model': 'model',
                    'language': 'ko',
                    'temperature': 0,
                    'chunk_sec': 29
                }
            }
        },
        '.wav': {
            'mode': ProcessorMode.BASIC,
            'processor': 'audio',
            'description': 'Audio transcription with Whisper',
            'options': {
                'whisper': {
                    'model': 'model',
                    'language': 'ko',
                    'temperature': 0,
                    'chunk_sec': 29
                }
            }
        },
        
        # 테이블: 기본형
        '.csv': {
            'mode': ProcessorMode.BASIC,
            'processor': 'tabular',
            'description': 'Tabular data processing',
            'options': {}
        },
        '.xlsx': {
            'mode': ProcessorMode.BASIC,
            'processor': 'tabular',
            'description': 'Excel data processing',
            'options': {}
        },
        
        # 문서: 기본형 (LangChain)
        '.doc': {
            'mode': ProcessorMode.BASIC,
            'processor': 'langchain',
            'description': 'Word document with LangChain',
            'options': {
                'text_splitter': {
                    'chunk_size': 1000,
                    'chunk_overlap': 200
                }
            }
        },
        '.docx': {
            'mode': ProcessorMode.BASIC,
            'processor': 'langchain',
            'description': 'Word document with LangChain',
            'options': {
                'text_splitter': {
                    'chunk_size': 1000,
                    'chunk_overlap': 200
                }
            }
        },
        '.ppt': {
            'mode': ProcessorMode.BASIC,
            'processor': 'langchain',
            'description': 'PowerPoint with LangChain',
            'options': {
                'text_splitter': {
                    'chunk_size': 1000,
                    'chunk_overlap': 200
                }
            }
        },
        '.pptx': {
            'mode': ProcessorMode.BASIC,
            'processor': 'langchain',
            'description': 'PowerPoint with LangChain',
            'options': {
                'text_splitter': {
                    'chunk_size': 1000,
                    'chunk_overlap': 200
                }
            }
        },
        '.hwp': {
            'mode': ProcessorMode.BASIC,
            'processor': 'langchain',
            'description': 'HWP with LangChain',
            'options': {
                'text_splitter': {
                    'chunk_size': 1000,
                    'chunk_overlap': 200
                }
            }
        },
        '.txt': {
            'mode': ProcessorMode.BASIC,
            'processor': 'langchain',
            'description': 'Text file with LangChain',
            'options': {
                'text_splitter': {
                    'chunk_size': 1000,
                    'chunk_overlap': 200
                }
            }
        },
        '.json': {
            'mode': ProcessorMode.BASIC,
            'processor': 'langchain',
            'description': 'JSON file with LangChain',
            'options': {
                'text_splitter': {
                    'chunk_size': 1000,
                    'chunk_overlap': 200
                }
            }
        },
        '.md': {
            'mode': ProcessorMode.BASIC,
            'processor': 'langchain',
            'description': 'Markdown with LangChain',
            'options': {
                'text_splitter': {
                    'chunk_size': 1000,
                    'chunk_overlap': 200
                }
            }
        }
    }
    
    def __init__(self, config_file: Optional[str] = None):
        """
        Initialize processor configuration
        
        Args:
            config_file: Optional path to JSON configuration file
        """
        self.config = self.DEFAULT_CONFIG.copy()
        self.config_file = config_file
        
        # Load custom configuration if provided
        if config_file and os.path.exists(config_file):
            self.load_config(config_file)
    
    def load_config(self, config_file: str):
        """Load configuration from JSON file"""
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                custom_config = json.load(f)
            
            # Update configuration with custom values
            for ext, settings in custom_config.items():
                if ext in self.config:
                    if 'mode' in settings:
                        # Convert string to enum
                        mode_str = settings['mode'].upper()
                        if mode_str in ProcessorMode.__members__:
                            settings['mode'] = ProcessorMode[mode_str]
                    self.config[ext].update(settings)
                else:
                    # New extension
                    if 'mode' in settings:
                        mode_str = settings['mode'].upper()
                        if mode_str in ProcessorMode.__members__:
                            settings['mode'] = ProcessorMode[mode_str]
                    self.config[ext] = settings
            
            print(f"✅ Configuration loaded from {config_file}")
            
        except Exception as e:
            print(f"⚠️ Error loading config: {e}")
    
    def save_config(self, config_file: Optional[str] = None):
        """Save current configuration to JSON file"""
        save_path = config_file or self.config_file
        if not save_path:
            save_path = "processor_config.json"
        
        # Convert enum to string for JSON serialization
        export_config = {}
        for ext, settings in self.config.items():
            export_settings = settings.copy()
            if 'mode' in export_settings and isinstance(export_settings['mode'], ProcessorMode):
                export_settings['mode'] = export_settings['mode'].value
            export_config[ext] = export_settings
        
        with open(save_path, 'w', encoding='utf-8') as f:
            json.dump(export_config, f, indent=2, ensure_ascii=False)
        
        print(f"✅ Configuration saved to {save_path}")
    
    def set_processor_option(self, extension: str, option_path: str, value: Any):
        """
        Set specific processor option
        
        Args:
            extension: File extension (e.g., '.pdf')
            option_path: Dot-separated path to option (e.g., 'enrichment.enabled')
            value: New value for the option
        
        Example:
            config.set_processor_option('.pdf', 'enrichment.toc_max_tokens', 2000)
            config.set_processor_option('.pdf', 'pipeline.do_ocr', True)
        """
        if not extension.startswith('.'):
            extension = '.' + extension
        
        if extension not in self.config:
            self.config[extension] = {}
        
        if 'options' not in self.config[extension]:
            self.config[extension]['options'] = {}
        
        # Parse option path
        keys = option_path.split('.')
        target = self.config[extension]['options']
        
        # Navigate to the target option
        for key in keys[:-1]:
            if key not in target:
                target[key] = {}
            target = target[key]
        
        # Set the value
        target[keys[-1]] = value
    
    def get_processor_options(self, extension: str) -> Dict[str, Any]:
        """
        Get processor options for extension
        
        Args:
            extension: File extension
            
        Returns:
            Dictionary of processor options
        """
        if not extension.startswith('.'):
            extension = '.' + extension
        
        if extension in self.config and 'options' in self.config[extension]:
            return self.config[extension]['options']
        
        return {}
    
    def set_enrichment_enabled(self, extension: str, enabled: bool):
        """Enable/disable enrichment for extension"""
        self.set_processor_option(extension, 'enrichment.enabled', enabled)
    
    def set_chunking_options(self, extension: str, max_tokens: int = None, merge_peers: bool = None):
        """Set chunking options"""
        if max_tokens is not None:
            self.set_processor_option(extension, 'chunking.max_tokens', max_tokens)
        if merge_peers is not None:
            self.set_processor_option(extension, 'chunking.merge_peers', merge_peers)
    
    def set_ocr_enabled(self, extension: str, enabled: bool):
        """Enable/disable OCR for PDF processing"""
        self.set_processor_option(extension, 'pipeline.do_ocr', enabled)
    
    def set_mode(self, extension: str, mode: ProcessorMode):
        """
        Set processing mode for specific extension
        
        Args:
            extension: File extension (e.g., '.pdf')
            mode: ProcessorMode.BASIC or ProcessorMode.INTELLIGENT
        """
        if not extension.startswith('.'):
            extension = '.' + extension
        
        if extension not in self.config:
            self.config[extension] = {}
        
        self.config[extension]['mode'] = mode
        
        # Update processor based on mode
        if mode == ProcessorMode.INTELLIGENT:
            # 지능형 처리기 할당
            if extension in ['.pdf', '.hwpx']:
                self.config[extension]['processor'] = 'docling'
            elif extension in ['.docx', '.pptx']:
                self.config[extension]['processor'] = 'docling_extended'  # Future extension
        else:
            # 기본형 처리기 할당
            if extension in ['.pdf', '.docx', '.ppt', '.pptx', '.hwp', '.txt', '.json', '.md']:
                self.config[extension]['processor'] = 'langchain'
            elif extension in ['.mp3', '.m4a', '.wav']:
                self.config[extension]['processor'] = 'audio'
            elif extension in ['.csv', '.xlsx']:
                self.config[extension]['processor'] = 'tabular'
    
    def get_mode(self, extension: str) -> ProcessorMode:
        """Get processing mode for extension"""
        if not extension.startswith('.'):
            extension = '.' + extension
        
        if extension in self.config and 'mode' in self.config[extension]:
            return self.config[extension]['mode']
        
        return ProcessorMode.BASIC  # Default to basic
    
    def get_processor(self, extension: str) -> str:
        """Get processor name for extension"""
        if not extension.startswith('.'):
            extension = '.' + extension
        
        if extension in self.config and 'processor' in self.config[extension]:
            return self.config[extension]['processor']
        
        return 'langchain'  # Default processor
    
    def toggle_mode(self, extension: str):
        """Toggle between BASIC and INTELLIGENT mode"""
        current_mode = self.get_mode(extension)
        new_mode = ProcessorMode.INTELLIGENT if current_mode == ProcessorMode.BASIC else ProcessorMode.BASIC
        self.set_mode(extension, new_mode)
        return new_mode
    
    def set_all_intelligent(self):
        """Set all extensions to intelligent mode"""
        for ext in self.config:
            self.set_mode(ext, ProcessorMode.INTELLIGENT)
    
    def set_all_basic(self):
        """Set all extensions to basic mode"""
        for ext in self.config:
            self.set_mode(ext, ProcessorMode.BASIC)
    
    def get_status(self) -> Dict[str, Dict[str, Any]]:
        """Get current configuration status"""
        status = {}
        for ext, settings in self.config.items():
            status[ext] = {
                'mode': settings.get('mode', ProcessorMode.BASIC).value,
                'processor': settings.get('processor', 'unknown'),
                'description': settings.get('description', '')
            }
        return status
    
    def print_status(self):
        """Print current configuration in a readable format"""
        print("\n" + "=" * 70)
        print("📋 Processor Configuration Status")
        print("=" * 70)
        
        # Group by mode
        intelligent = []
        basic = []
        
        for ext, settings in self.config.items():
            mode = settings.get('mode', ProcessorMode.BASIC)
            processor = settings.get('processor', 'unknown')
            
            if mode == ProcessorMode.INTELLIGENT:
                intelligent.append(f"{ext:6s} → {processor:15s}")
            else:
                basic.append(f"{ext:6s} → {processor:15s}")
        
        print("\n🧠 INTELLIGENT Mode (지능형):")
        if intelligent:
            for item in intelligent:
                print(f"  {item}")
        else:
            print("  (None)")
        
        print("\n📄 BASIC Mode (기본형):")
        if basic:
            for item in basic:
                print(f"  {item}")
        else:
            print("  (None)")
        
        print("=" * 70)