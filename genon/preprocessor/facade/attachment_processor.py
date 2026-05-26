from pathlib import Path
from typing import Any, List

from fastapi import Request
from docling_core.transforms.chunker import DocChunk
from docling_core.types import DoclingDocument

from genon.preprocessor.facade.loaders import DoclingLoader
from genon.preprocessor.facade.chunkers import CHUNKERS
from genon.preprocessor.facade.metadata import GenOSVectorMetaBuilder
from genon.preprocessor.facade.utils.config_util import load_yaml_config
from genon.preprocessor.facade.utils.logging_utils import setup_logging

CONFIG_FILENAME = "attachment_config.yaml"

DEFAULT_FORMAT_OPTIONS = {
    "pdf": {"backend": "langchain_pdf"},
    "docx": {"pipeline_options": "simple", "backend": "msword"},
    "hwpx": {"pipeline_options": "simple", "backend": "hwpx"},
    "pptx": {"backend": "langchain_pptx"},
    "md": {"pipeline_options": "simple", "backend": "langchain_md"},
    "csv": {"backend": "tabular"},
    "xlsx": {"backend": "tabular"},
    "mp3": {
        "backend": "audio",
        "req_url": "http://whisper-service/api",
        "req_data": {"language": "ko"},
        "chunk_sec": 29,
    },
    "wav": {
        "backend": "audio",
        "req_url": "http://whisper-service/api",
        "req_data": {"language": "ko"},
        "chunk_sec": 29,
    },
}


class DocumentProcessor:

    def __init__(self) -> None:
        self.config = load_yaml_config(
            filename=CONFIG_FILENAME,
            caller_file=__file__
        )
        setup_logging(int(self.config.get("log_level", 0)))

        format_options = {**DEFAULT_FORMAT_OPTIONS, **self.config.get("format_options", {})}
        self._ext_loaders = self._build_ext_loaders(format_options, self.config.get("resource_path"))
        self._chunker = self._build_chunker(self.config["chunker"])
        self.genos_meta_builder = GenOSVectorMetaBuilder()

    def _build_chunker(self, chunker_cfg: dict | str):
        if isinstance(chunker_cfg, dict):
            name = chunker_cfg["name"]
            options = {k: v for k, v in chunker_cfg.items() if k != "name"}
        else:
            name, options = chunker_cfg, {}
        return CHUNKERS[name](**options)

    def _build_ext_loaders(self, format_options: dict, resource_path: str | None = None) -> dict:
        docling_loader = DoclingLoader(format_options, resource_path=resource_path)
        return {ext: docling_loader for ext in format_options}

    def load_documents(self, file_path: str, **kwargs) -> Any:
        ext = Path(file_path).suffix.lstrip(".").lower()
        loader = self._ext_loaders.get(ext)
        assert loader is not None, (
            f"지원하지 않는 확장자: .{ext}. "
            f"format_options에 추가하세요. 현재 등록된 확장자: {list(self._ext_loaders.keys())}"
        )
        return loader.load(file_path)

    def split_documents(self, documents: DoclingDocument, **kwargs) -> list[DocChunk]:
        return list(self._chunker.chunk(documents, **kwargs))

    async def compose_vectors(
        self, request: Request, file_path: str, document: DoclingDocument, chunks: List[DocChunk], **kwargs
    ) -> list[dict]:
        return await self.genos_meta_builder(document, chunks, file_path, request, **kwargs)

    async def __call__(self, request: Request, file_path: str, **kwargs) -> Any:

        documents = self.load_documents(file_path, **kwargs)

        chunks = self.split_documents(documents, **kwargs)

        vectors = await self.compose_vectors(request, file_path, documents, chunks, **kwargs)
        return vectors
