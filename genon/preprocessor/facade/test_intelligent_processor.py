from facade.base_processor import BaseProcessor

config = {
    "format_options": {
        "pdf": {
            "pipeline_options": "pdf",
            "backend": "pypdf",
            "generate_picture_images": True,
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
        "hwpx": {"converter": "libreoffice"},
        "pptx": {"converter": "libreoffice"},
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
