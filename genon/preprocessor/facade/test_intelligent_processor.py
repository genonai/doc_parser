from facade.base_processor import BaseProcessor

config = {
    "format_options": {
        "pdf": {
            "pipeline_options": "pdf",
            "backend": "pypdf",
            "generate_picture_images": True,
            "do_ocr": "easy",  # configs/ocr/easy.yaml
        },
        "docx": {
            "pipeline_options": "simple",
            "backend": "msword",
        },
        "md": {
            "pipeline_options": "simple",
            "backend": "md",
        },
        "html": {
            "pipeline_options": "simple",
            "backend": "html",
        },
        "hwpx": {
            "pipeline_options": "simple",
            "backend": "hwpx",  # | hwp_sdk | 사이냅스 | 자유소프트
        },
        "pptx": {
            "pipeline_options": "simple",
            "backend": "mspowerpoint",
        },
    },
    "chunker": {"name": "smart", "max_tokens": 1024},
    "enrichers": [],
    "return_level": "vector",
    "log_level": 4,
}


class DocumentProcessor(BaseProcessor):
    def __init__(self, config=config):
        super().__init__(config)
