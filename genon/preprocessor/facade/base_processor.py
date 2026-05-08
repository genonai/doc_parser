from pathlib import Path
from typing import Any, List

from fastapi import Request
from docling_core.transforms.chunker import BaseChunker, DocChunk
from docling_core.types import DoclingDocument

from genon.preprocessor.facade.loaders import DoclingLoader, TabularLoader, AudioLoader, LOADERS
from genon.preprocessor.facade.chunkers import CHUNKERS
from genon.preprocessor.facade.metadata import GenOSVectorMetaBuilder
from genon.preprocessor.facade.utils.logging_utils import setup_logging


class BaseProcessor:
    config: dict = None

    def __init__(self, config: dict) -> None:
        self.config = config
        setup_logging(int(config.get("log_level", 0)))
        self.return_level = config.get("return_level", "vector")

        assert self.return_level in [
            "document",
            "chunk",
            "vector",
        ], f"다음 리턴 레벨 중에서 골라주세요: document | chunk | vector, 현재 리턴 레벨: {self.return_level}"

        self._ext_loaders = self._build_ext_loaders(config["format_options"])
        self.chunker = CHUNKERS[config["chunker"]]()
        self.enrichers = []  # 청킹 전: TOC, 작성일 등 document 레벨 enrichment
        self.genos_meta_builder = GenOSVectorMetaBuilder()

    def _build_ext_loaders(self, format_options: dict) -> dict:
        """확장자 → loader 인스턴스 매핑 빌드.

        format_options의 각 확장자 옵션에서:
          - "loader": "tabular"  → TabularLoader
          - "loader": "audio"    → AudioLoader(opt)
          - 나머지 (native or "converter" key) → 공유 DoclingLoader 인스턴스
        """
        docling_formats = {}
        ext_loaders = {}

        for ext, opt in format_options.items():
            loader_key = opt.get("loader")
            if loader_key is not None:
                loader_cls = LOADERS.get(loader_key)
                assert loader_cls is not None, f"알 수 없는 loader: {loader_key}. 가능한 값: {list(LOADERS.keys())}"
                ext_loaders[ext] = loader_cls(opt)
            else:
                docling_formats[ext] = opt

        if docling_formats:
            docling_loader = DoclingLoader(docling_formats)
            for ext in docling_formats:
                ext_loaders[ext] = docling_loader
            # converter 키가 있는 확장자도 DoclingLoader가 내부에서 처리
            for ext, opt in format_options.items():
                if "converter" in opt and ext not in ext_loaders:
                    ext_loaders[ext] = docling_loader

        return ext_loaders

    def load_documents(self, file_path: str, **kwargs) -> Any:
        ext = Path(file_path).suffix.lstrip(".").lower()
        loader = self._ext_loaders.get(ext)
        assert loader is not None, (
            f"지원하지 않는 확장자: .{ext}. "
            f"format_options에 추가하세요. 현재 등록된 확장자: {list(self._ext_loaders.keys())}"
        )
        return loader.load(file_path)

    def split_documents(self, documents: DoclingDocument, **kwargs) -> list[DocChunk]:
        return list(self.chunker.chunk(documents, **kwargs))

    def postprocessing(self, chunks: list[DocChunk], documents: DoclingDocument, **kwargs) -> list[DocChunk]:
        # 오버라이드 하여 구현
        return chunks

    async def compose_vectors(
        self, request: Request, file_path: str, document: DoclingDocument, chunks: List[DocChunk], **kwargs
    ) -> list[dict]:
        return await self.genos_meta_builder(document, chunks, file_path, request, **kwargs)

    async def __call__(self, request: Request, file_path: str, **kwargs) -> Any:
        result = self.load_documents(file_path, **kwargs)

        # tabular/audio: DoclingDocument가 아니면 이후 단계 건너뜀
        # 추후 해당 확장자도 DoclingDocument로 나와야 함
        if not isinstance(result, DoclingDocument):
            return result

        # 청킹 전 enrichment (TOC, 작성일 등 document 레벨)
        for enricher in self.enrichers:
            documents = await enricher.enrich(documents, **kwargs)

        documents = result
        if self.return_level == "document":
            return documents

        chunks = self.split_documents(documents, **kwargs)
        if self.return_level == "chunk":
            return chunks

        # 청킹 후 enrichment (테이블 정제, 이미지 설명 등 chunk 레벨)
        chunks = self.postprocessing(chunks, documents, **kwargs)

        vectors = await self.compose_vectors(request, file_path, documents, chunks, **kwargs)
        return vectors
