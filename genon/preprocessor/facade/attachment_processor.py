from pathlib import Path
from typing import Any, List

import yaml
from fastapi import Request
from docling_core.transforms.chunker import DocChunk
from docling_core.types import DoclingDocument

from genon.preprocessor.facade.loaders import DoclingLoader
from genon.preprocessor.facade.chunkers import CHUNKERS
from genon.preprocessor.facade.utils.logging_utils import setup_logging

config = yaml.safe_load(Path("/app/resource/config.yaml").read_text(encoding="utf-8"))

DEFAULT_FORMAT_OPTIONS = {
    "pdf":  {"backend": "langchain_pdf"},
    "docx": {"pipeline_options": "simple", "backend": "msword"},
    "hwpx": {"pipeline_options": "simple", "backend": "hwpx"},
    "pptx": {"backend": "langchain_pptx"},
    "md":   {"pipeline_options": "simple", "backend": "langchain_md"},
    "csv":  {"backend": "tabular"},
    "xlsx": {"backend": "tabular"},
    "mp3":  {"backend": "audio", "req_url": "http://whisper-service/api", "req_data": {"language": "ko"}, "chunk_sec": 29},
    "wav":  {"backend": "audio", "req_url": "http://whisper-service/api", "req_data": {"language": "ko"}, "chunk_sec": 29},
}


class DocumentProcessor:
    config: dict = config

    def __init__(self, config: dict) -> None:
        self.config = config
        setup_logging(int(config.get("log_level", 0)))
        self.return_level = config.get("return_level", "vector")

        assert self.return_level in [
            "document",
            "chunk",
            "vector",
        ], f"다음 리턴 레벨 중에서 골라주세요: document | chunk | vector, 현재 리턴 레벨: {self.return_level}"

        format_options = {**DEFAULT_FORMAT_OPTIONS, **config.get("format_options", {})}
        self._ext_loaders = self._build_ext_loaders(format_options, config.get("resource_path"))
        self._default_chunker = self._build_chunker(config["chunker"])
        self._ext_chunkers = self._build_ext_chunkers(format_options, config["chunker"])

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

    def _build_ext_chunkers(self, format_options: dict, default_chunker: dict | str) -> dict:
        return {ext: self._build_chunker(opt.get("chunker", default_chunker)) for ext, opt in format_options.items()}

    def load_documents(self, file_path: str, **kwargs) -> Any:
        ext = Path(file_path).suffix.lstrip(".").lower()
        loader = self._ext_loaders.get(ext)
        assert loader is not None, (
            f"지원하지 않는 확장자: .{ext}. "
            f"format_options에 추가하세요. 현재 등록된 확장자: {list(self._ext_loaders.keys())}"
        )
        return loader.load(file_path)

    def split_documents(self, documents: DoclingDocument, file_path: str = "", **kwargs) -> list[DocChunk]:
        ext = Path(file_path).suffix.lstrip(".").lower() if file_path else ""
        chunker = self._ext_chunkers.get(ext, self._default_chunker)
        return list(chunker.chunk(documents, **kwargs))

    async def compose_vectors(
        self, request: Request, file_path: str, document: DoclingDocument, chunks: List[DocChunk], **kwargs
    ) -> list[dict]:
        return await self.genos_meta_builder(document, chunks, file_path, request, **kwargs)

    async def __call__(self, request: Request, file_path: str, **kwargs) -> Any:

        return_level = kwargs.get("return_level", self.return_level)

        documents = self.load_documents(file_path, **kwargs)
        if return_level == "document":
            return documents

        chunks = self.split_documents(documents, file_path=file_path, **kwargs)
        if return_level == "chunk":
            return chunks

        vectors = await self.compose_vectors(request, file_path, documents, chunks, **kwargs)
        return vectors
