# from typing import Any

# from fastapi import Request
# from langchain_core.documents import Document

from base_processor import BaseProcessor


ocr_config = {
    "model": "~~~~~~~~~",  # easy, paddle
    "end_point": "~~~~",
}

vlm_layout_config = {
    "model": "~~~~~~~~",
    "end_point": "~~~~~~~~~",
}

vlm_toc_config = {
    "model": "~~~~~~~~",
    "end_point": "~~~~~~~~~",
}


pdf_config = {
    "pipeline_options": "~~~~~",
    "backend": "~~~~",
}

toc_config = {
    "pipeline_options": "~~~~~",
    "backend": "~~~~",
}


config = {
    # TODO: ["pdf", "hwp", "hwpx", "doc", "docx", "xlsx", "csv", "ppt", "pptx", "md", "json", "html"], + image
    "format_options": {
        "pdf": {
            "pipeline_options": "pdf",  # simple | pdf
            "backend": "pypdf",  #
            "generate_picture_images": True,  # pdf일때만 설정 가능한듯
        },
        "docx": {
            "pipeline_options": "simple",
            "backend": "msword",
        },
    },
    "chunker": "simple",  # TODO: static bucket, dynamic bucket
}


class DocumentProcessor(BaseProcessor):
    def __init__(self):
        super().__init__(config)
