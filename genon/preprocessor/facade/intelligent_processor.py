import inspect
import logging
from pathlib import Path
from typing import Any, List

from fastapi import Request
from docling_core.transforms.chunker import DocChunk
from docling_core.types import DoclingDocument

from genon.preprocessor.facade.loaders import DoclingLoader
from genon.preprocessor.facade.chunkers import CHUNKERS
from genon.preprocessor.facade.enrichment import ENRICHERS
from genon.preprocessor.facade.metadata import GenOSVectorMetaBuilder
from genon.preprocessor.facade.postprocessing import POSTPROCESSORS
from genon.preprocessor.facade.utils.config_util import load_yaml_config
from genon.preprocessor.facade.utils.logging_utils import setup_logging
from genon.preprocessor.facade.utils.parse_serializer import (
    normalize_output_format,
    normalize_table_format,
    normalize_response,
    build_docling_response,
)

CONFIG_FILENAME = "intelligent_config.yaml"

_log = logging.getLogger(__name__)


class DocumentProcessor:

    def __init__(self) -> None:
        self.config = load_yaml_config(
            filename=CONFIG_FILENAME,
            caller_file=__file__
        )
        setup_logging(int(self.config.get("log_level", 0)))

        self._ext_loaders = self._build_ext_loaders(self.config["format_options"], self.config.get("resource_path"))
        self._chunker = self._build_chunker(self.config["chunker"])
        self.enrichers = self._build_enrichers(
            self.config.get("enrichers", []),
            resource_path=self.config.get("resource_path"),
        )
        self.genos_meta_builder = GenOSVectorMetaBuilder()
        self.postprocessors = self._build_postprocessors(
            self.config.get("postprocessors", []),
        )
        output_cfg = self.config.get("output", {})
        self._output_format = normalize_output_format(output_cfg.get("format", "json"))
        self._table_format = normalize_table_format(output_cfg.get("table_format", "html"))

    @staticmethod
    def _normalize_enricher_name(name: str) -> str:
        return name[:-9] if name.endswith("_enricher") else name

    def _build_postprocessors(self, postprocessors_cfg: list) -> list:
        if not postprocessors_cfg:
            return []
        result = []
        for item in postprocessors_cfg:
            if isinstance(item, dict):
                name, options = next(iter(item.items()))
                options = {k: v for k, v in (options or {}).items() if v is not None}
            else:
                name, options = item, {}
            cls = POSTPROCESSORS.get(name)
            assert cls is not None, f"지원하지 않는 postprocessor: {name}. 가능한 값: {list(POSTPROCESSORS.keys())}"
            result.append(cls(**options))
        return result

    def _build_enrichers(
        self,
        enrichers_cfg: list,
        resource_path: str | None = None,
    ) -> list:
        if not enrichers_cfg:
            return []
        result = []
        for item in enrichers_cfg:
            if isinstance(item, dict):
                name, options = next(iter(item.items()))
                options = {k: v for k, v in (options or {}).items() if v is not None}
            else:
                name, options = item, {}
            name = self._normalize_enricher_name(name)
            cls = ENRICHERS.get(name)
            assert cls is not None, f"지원하지 않는 enricher: {name}. 가능한 값: {list(ENRICHERS.keys())}"
            accepts = inspect.signature(cls.__init__).parameters
            if "resource_path" not in options and resource_path and "resource_path" in accepts:
                options["resource_path"] = resource_path
            result.append(cls(**options))
        return result

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

    async def postprocessing(self, chunks: list[DocChunk], documents: DoclingDocument, **kwargs) -> list[DocChunk]:
        for postprocessor in self.postprocessors:
            chunks = await postprocessor.process(chunks, documents, **kwargs)
        return chunks

    async def compose_vectors(
        self, request: Request, file_path: str, document: DoclingDocument, chunks: List[DocChunk], **kwargs
    ) -> list[dict]:
        return await self.genos_meta_builder(document, chunks, file_path, request, **kwargs)

    async def __call__(self, request: Request, file_path: str, **kwargs) -> Any:

        enrichment_context = kwargs.get("_enrichment_context", {})
        if not isinstance(enrichment_context, dict):
            enrichment_context = {}
        enrichment_context.setdefault("custom_metadata", {})

        documents = self.load_documents(file_path, **kwargs)

        # 청킹 전 enrichment (TOC, 작성일 등 document 레벨)
        for enricher in self.enrichers:
            documents = await enricher.enrich(
                documents,
                _enrichment_context=enrichment_context,
                **kwargs,
            )

        chunks = self.split_documents(documents, **kwargs)

        # 청킹 후 postprocessing (테이블 정제 등 chunk 레벨)
        chunks = await self.postprocessing(chunks, documents, file_path=file_path, _enrichment_context=enrichment_context, **kwargs)

        vectors = await self.compose_vectors(
            request,
            file_path,
            documents,
            chunks,
            _enrichment_context=enrichment_context,
            **kwargs,
        )
        return vectors
