from genon.preprocessor.facade.base_processor import BaseProcessor

config = {
    "format_options": {
        "pdf": {"pipeline_options": "pdf", "backend": "pypdf", "generate_picture_images": True},
        "docx": {"pipeline_options": "simple", "backend": "msword"},
        "hwp": {"converter": "libreoffice"},
        "hwpx": {"converter": "libreoffice"},
        "pptx": {"converter": "libreoffice"},
        "png": {"converter": "image"},
        "jpg": {"converter": "image"},
        "jpeg": {"converter": "image"},
        "csv": {"loader": "tabular"},
        "xlsx": {"loader": "tabular"},
        "wav": {"loader": "audio", "req_url": "http://whisper-service/api", "req_data": {"language": "ko"}},
        "mp3": {"loader": "audio", "req_url": "http://whisper-service/api", "req_data": {"language": "ko"}},
    },
    "chunker": "smart",
    "return_level": "vector",
    "log_level": 4,
}


class DocumentProcessor(BaseProcessor):
    def __init__(self, config=config):
        super().__init__(config)


if __name__ == "__main__":
    proc = DocumentProcessor()
    for ext, loader in proc._ext_loaders.items():
        print(f"  .{ext:6s} → {type(loader).__name__}")
    print("OK")
