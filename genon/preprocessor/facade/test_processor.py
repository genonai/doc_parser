from genon.preprocessor.facade.base_processor import BaseProcessor

# Config A: docling 직접 로드 (PDF/DOCX/MD/HTML)
config_docling = {
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
    },
    "chunker": "bucket",
    "return_level": "vector",
    "log_level": 4,
}

# Config B: converter 포함 (HWPX/PPTX → LibreOffice, PNG → image)
config_with_converters = {
    "format_options": {
        "pdf": {
            "pipeline_options": "pdf",
            "backend": "pypdf",
            "generate_picture_images": True,
        },
        "hwpx": {"converter": "libreoffice"},
        "pptx": {"converter": "libreoffice"},
        "png":  {"converter": "image"},
    },
    "chunker": "bucket",
    "return_level": "vector",
    "log_level": 4,
}

# Config C: attachment — attachment_processor와 동일 동작
# (문서 포맷 + 표형식 + 오디오 전부 포함)
config_attachment = {
    "format_options": {
        # 문서 포맷 (docling 직접 로드)
        "pdf":  {"pipeline_options": "pdf", "backend": "pypdf", "generate_picture_images": True},
        "docx": {"pipeline_options": "simple", "backend": "msword"},
        # 문서 포맷 (LibreOffice → PDF → docling)
        "hwp":  {"converter": "libreoffice"},   # or {"loader": "genos_hwp"}
        "hwpx": {"converter": "libreoffice"},   # or {"loader": "genos_hwp"}
        "pptx": {"converter": "libreoffice"},
        # 이미지 포맷 (image → PDF → docling)
        "png":  {"converter": "image"},
        "jpg":  {"converter": "image"},
        "jpeg": {"converter": "image"},
        # 표형식
        "csv":  {"loader": "tabular"},
        "xlsx": {"loader": "tabular"},
        # 오디오
        "wav":  {"loader": "audio", "req_url": "http://whisper-service/api", "req_data": {"language": "ko"}},
        "mp3":  {"loader": "audio", "req_url": "http://whisper-service/api", "req_data": {"language": "ko"}},
    },
    "chunker": "bucket",
    "return_level": "vector",
    "log_level": 4,
}


class DocumentProcessor(BaseProcessor):
    def __init__(self, config=config_docling):
        super().__init__(config)


if __name__ == "__main__":
    from genon.preprocessor.facade.loaders import DoclingLoader, TabularLoader, AudioLoader

    configs = [
        ("Config A: docling only",     config_docling),
        ("Config B: with converters",  config_with_converters),
        ("Config C: attachment",        config_attachment),
    ]

    for label, cfg in configs:
        print(f"=== {label} ===")
        proc = DocumentProcessor(cfg)
        for ext, loader in proc._ext_loaders.items():
            print(f"  .{ext:6s} → {type(loader).__name__}")
        print()

    print("OK: 세 config 모두 정상 초기화")
