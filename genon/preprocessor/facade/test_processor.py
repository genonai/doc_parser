from genon.preprocessor.facade.base_processor import BaseProcessor

# TODO: ["hwp", "hwpx", "doc", "docx", "xlsx", "csv", "ppt", "pptx", "md", "json", "html"], + image
config = {
    "format_options": {
        "pdf": {
            "pipeline_options": "pdf",  # simple | pdf
            "backend": "pypdf",  # pypdf | pymu
            "generate_picture_images": True,  # backend가 pypdf일때만 가능, 반드시 True로 해놔야 함.
            # "save_images": True,
            # table : None | tableformer | dots_ocr
            # toc: None | {"endpoint": "~~~~", "prompt": "~~~~~"}
            # ocr: None | {"model": "easy | paddle", "endpoint" "~~~", "token": "~~~~"}
            # image_description
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
    "chunker": "char_bucket",  # simple | char_bucket | bucket
    "return_level": "vector",  # document | chunk | vector
    "log_level": 4,
    # generate_picture_images: 모든 확장자에 대해서 갖고 있어야 하지 않나?
    # metadata..?
}


class DocumentProcessor(BaseProcessor):
    def __init__(self):
        super().__init__(config)
