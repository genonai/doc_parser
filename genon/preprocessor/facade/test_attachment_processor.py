from genon.preprocessor.facade.base_processor import BaseProcessor


config = {
    "format_options": {
        "pdf":  {"backend": "langchain_pdf",  "chunker": "recursive"},
        "docx": {"pipeline_options": "simple", "backend": "msword"},
        "hwpx": {"pipeline_options": "simple", "backend": "hwpx"},
        "pptx": {"backend": "langchain_pptx", "chunker": "recursive"},
        "md":   {"backend": "langchain_md", "chunker": "recursive"},
        "csv":  {"backend": "tabular"},
        "xlsx": {"backend": "tabular"},
    },
    "chunker": "hybrid",
    "return_level": "vector",
    "log_level": 4,
}


class DocumentProcessor(BaseProcessor):
    def __init__(self, config=config):
        super().__init__(config)
