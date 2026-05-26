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

_SUBJECT_SYSTEM_PROMPT = """당신은 문서 분석 전문가입니다.
입력된 문서를 분석하여 아래 JSON 형식으로만 출력하세요.

{
  "subject": "제작처가 포함된 300자 이내의 주제 요약",
  "category": "산출방법서" or "약관" or "기타",
  "title": "문서 제목"
}"""

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
            genos_url=self.config.get("genos_url", ""),
        )
        self.genos_meta_builder = GenOSVectorMetaBuilder()
        self.postprocessors = self._build_postprocessors(
            self.config.get("postprocessors", []),
            genos_url=self.config.get("genos_url", ""),
        )
        output_cfg = self.config.get("output", {})
        self._output_format = normalize_output_format(output_cfg.get("format", "json"))
        self._table_format = normalize_table_format(output_cfg.get("table_format", "html"))

    def _build_postprocessors(self, postprocessors_cfg: list, genos_url: str = "") -> list:
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
            accepts = inspect.signature(cls.__init__).parameters
            if "genos_url" not in options and genos_url and "genos_url" in accepts:
                options["genos_url"] = genos_url
            result.append(cls(**options))
        return result

    def _build_enrichers(self, enrichers_cfg: list, genos_url: str = "") -> list:
        if not enrichers_cfg:
            return []
        result = []
        for item in enrichers_cfg:
            if isinstance(item, dict):
                name, options = next(iter(item.items()))
                options = {k: v for k, v in (options or {}).items() if v is not None}
            else:
                name, options = item, {}
            cls = ENRICHERS.get(name)
            assert cls is not None, f"지원하지 않는 enricher: {name}. 가능한 값: {list(ENRICHERS.keys())}"
            accepts = inspect.signature(cls.__init__).parameters
            if "genos_url" not in options and genos_url and "genos_url" in accepts:
                options["genos_url"] = genos_url
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
        docling_loader = DoclingLoader(
            format_options, resource_path=resource_path, genos_url=self.config.get("genos_url", "")
        )
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

        # ── 커스터마이즈 예시: subject 추출 → VLM 이미지 설명 생성 ─────────────
        # 아래 변수만 바꿔서 바로 사용 가능
        # import asyncio, base64, json, fitz, httpx
        # from docling_core.types.doc import PictureItem
        #
        # SERVING_ID = "729"
        # BEARER_TOKEN = ""
        # GENOS_URL = "https://genos.genon.ai"
        # IMAGE_PROMPT = "문서 주제: {subject}\n이미지를 간결하고 정확하게 설명하세요."
        #
        # url = f"{GENOS_URL}/api/gateway/rep/serving/{SERVING_ID}/v1/chat/completions"
        # headers = {"Content-Type": "application/json", "Authorization": f"Bearer {BEARER_TOKEN}"}
        # file_path = kwargs.get("file_path", "")
        #
        # # 1. subject 추출
        # subject, full_text = "", ""
        # with fitz.open(file_path) as doc:
        #     for page in doc:
        #         full_text += page.get_text("text") + "\n"
        #         if len(full_text) >= 12000:
        #             break
        # async with httpx.AsyncClient(timeout=60) as client:
        #     resp = await client.post(url, headers=headers, json={
        #         "messages": [{"role": "system", "content": _SUBJECT_SYSTEM_PROMPT},
        #                       {"role": "user", "content": full_text}],
        #         "max_tokens": 1000, "temperature": 0.0,
        #         "response_format": {"type": "json_object"},
        #     })
        #     subject = json.loads(resp.json()["choices"][0]["message"]["content"]).get("subject", "")
        #
        # # 2. PictureItem 청크 → VLM 호출
        # prompt = IMAGE_PROMPT.format(subject=subject)
        # tasks, indices = [], []
        # for idx, chunk in enumerate(chunks):
        #     pic = next((i for i in (getattr(chunk.meta, "doc_items", None) or []) if isinstance(i, PictureItem)), None)
        #     if pic is None or pic.image is None or pic.image.uri is None:
        #         continue
        #     uri = str(pic.image.uri)
        #     mimetype = pic.image.mimetype or "image/png"
        #     encoded = uri.split(",", 1)[1] if uri.startswith("data:") else base64.b64encode(Path(uri).read_bytes()).decode()
        #
        #     async def _call(u=url, h=headers, pr=prompt, mt=mimetype, enc=encoded):
        #         async with httpx.AsyncClient(timeout=90) as c:
        #             r = await c.post(u, headers=h, json={
        #                 "max_tokens": 1000, "temperature": 0.1,
        #                 "messages": [{"role": "user", "content": [
        #                     {"type": "text", "text": pr},
        #                     {"type": "image_url", "image_url": {"url": f"data:{mt};base64,{enc}"}},
        #                 ]}],
        #             })
        #             r.raise_for_status()
        #             return r.json()["choices"][0]["message"]["content"]
        #     tasks.append(_call())
        #     indices.append(idx)
        #
        # for i, result in enumerate(await asyncio.gather(*tasks, return_exceptions=True)):
        #     if not isinstance(result, Exception):
        #         chunks[indices[i]].text = result

        return chunks

    async def compose_vectors(
        self, request: Request, file_path: str, document: DoclingDocument, chunks: List[DocChunk], **kwargs
    ) -> list[dict]:
        return await self.genos_meta_builder(document, chunks, file_path, request, **kwargs)

    async def __call__(self, request: Request, file_path: str, **kwargs) -> Any:

        documents = self.load_documents(file_path, **kwargs)

        # 청킹 전 enrichment (TOC, 작성일 등 document 레벨)
        for enricher in self.enrichers:
            documents = await enricher.enrich(documents, **kwargs)

        chunks = self.split_documents(documents, **kwargs)

        # 청킹 후 postprocessing (테이블 정제 등 chunk 레벨)
        chunks = await self.postprocessing(chunks, documents, file_path=file_path, **kwargs)

        vectors = await self.compose_vectors(request, file_path, documents, chunks, **kwargs)
        return vectors
