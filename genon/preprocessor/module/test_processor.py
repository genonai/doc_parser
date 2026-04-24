from base_processor import BaseProcessor


# TODO: ["pdf", "hwp", "hwpx", "doc", "docx", "xlsx", "csv", "ppt", "pptx", "md", "json", "html"], + image
config = {
    "format_options": {
        "pdf": {
            "pipeline_options": "pdf",  # simple | pdf
            "backend": "pypdf",  # pypdf | pymu
            "generate_picture_images": True,  # backend가 pypdf일때만 가능, 반드시 True로 해놔야 함.
            # table : None | tableformer | dots_ocr
            # toc: None | {"endpoint": "~~~~", "prompt": "~~~~~"}
            # ocr: None | {"model": "easy | paddle", "endpoint" "~~~", "token": "~~~~"}
            # image_description
        },
        "docx": {
            "pipeline_options": "simple",
            "backend": "msword",
        },
    },
    "chunker": "char_bucket",  # simple | char_bucket | bucket
    "return_level": "vector",  # document | chunk | vector
    "log_level": 4,
    # metadata..?
}


class DocumentProcessor(BaseProcessor):
    def __init__(self):
        super().__init__(config)
