from genon.preprocessor.facade.base_processor import BaseProcessor

# attachment_processor는 pipeline_options가 안필요함.
# converter보다는 loaders가 필요해보임.

config = {
    "format_options": {
        "pdf": {"pipeline_options": "simple", "backend": "pymu"},
        "docx": {"pipeline_options": "simple", "backend": "msword"},
        "hwp": {"converter": "libreoffice"},
        "hwpx": {"pipeline_options": "simple", "backend": "hwpx"},
        "pptx": {"converter": "libreoffice"},
        "md": {"pipeline_options": "simple", "backend": "md"},
        "png": {"converter": "image"},
        "jpg": {"converter": "image"},
        "jpeg": {"converter": "image"},
        "csv": {"loader": "tabular"},
        "xlsx": {"loader": "tabular"},
        "wav": {"loader": "audio", "req_url": "http://whisper-service/api", "req_data": {"language": "ko"}},
        "mp3": {"loader": "audio", "req_url": "http://whisper-service/api", "req_data": {"language": "ko"}},
    },
    "chunker": "hybrid",
    "return_level": "vector",
    "log_level": 4,
}


class DocumentProcessor(BaseProcessor):
    def __init__(self, config=config):
        super().__init__(config)
