"""
EnhancedImageDescriptionPostprocessor:
청킹 후 PictureItem 청크를 찾아 VLM으로 상세 설명(+ 차트/표 마크다운 변환)을 생성하고
chunk.text 를 교체한다.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path

import httpx
from docling_core.transforms.chunker import DocChunk
from docling_core.types import DoclingDocument
from docling_core.types.doc import PictureItem

_log = logging.getLogger(__name__)


class EnhancedImageDescriptionPostprocessor:
    """
    PictureItem 청크에 대해 LLM으로 상세 이미지 설명 + 차트/표 마크다운 변환을 생성하고
    chunk.text 를 교체한다.

    config 예시 (yaml 인라인):
        postprocessors:
          - enhanced_image_description:
              url: "https://..."
              api_key: null
              model: "google/gemini-2.0-flash"
              max_tokens: 5000
              temperature: 0.1
              timeout: 120
              batch_size: 3
              prompt: |
                당신은 RAG 시스템을 위한 이미지 설명 전문가입니다.
                문서의 주제 : {subject}
                ...
    """

    def __init__(
        self,
        url: str = "",
        api_key: str = "",
        model: str = "",
        max_tokens: int = 5000,
        temperature: float = 0.1,
        timeout: int = 120,
        batch_size: int = 3,
        prompt: str = "",
    ):
        self._url = url
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._timeout = timeout
        self._batch_size = batch_size
        self._prompt_template = prompt.strip()
        self._headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"

    # ------------------------------------------------------------------
    # 이미지 추출
    # ------------------------------------------------------------------

    @staticmethod
    def _get_image_base64(item: PictureItem) -> tuple[str, str] | None:
        """(mimetype, base64_data) 반환. 이미지 없으면 None."""
        if item.image is None or item.image.uri is None:
            return None

        uri = str(item.image.uri)
        mimetype = item.image.mimetype or "image/png"

        if uri.startswith("data:"):
            _, encoded = uri.split(",", 1)
            return mimetype, encoded

        path = Path(uri)
        if not path.exists():
            return None
        encoded = base64.b64encode(path.read_bytes()).decode()
        return mimetype, encoded

    # ------------------------------------------------------------------
    # LLM 호출
    # ------------------------------------------------------------------

    async def _call_llm(self, mimetype: str, image_b64: str, subject: str = "") -> str:
        prompt = self._prompt_template.format(subject=subject or "")
        payload = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mimetype};base64,{image_b64}"},
                        },
                    ],
                }
            ],
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(self._url, json=payload, headers=self._headers)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]

    # ------------------------------------------------------------------
    # 단일 청크 처리 (chunk_idx 반환)
    # ------------------------------------------------------------------

    async def _process_chunk(
        self, chunk_idx: int, item: PictureItem, subject: str
    ) -> tuple[int, str | None]:
        result = self._get_image_base64(item)
        if result is None:
            return chunk_idx, None
        mimetype, image_b64 = result
        try:
            description = await self._call_llm(mimetype, image_b64, subject=subject)
            return chunk_idx, description
        except Exception as e:
            _log.warning("enhanced_image_description 실패 chunk=%s ref=%s error=%s", chunk_idx, item.self_ref, e)
            return chunk_idx, None

    # ------------------------------------------------------------------
    # 배치 처리
    # ------------------------------------------------------------------

    async def _run_in_batches(self, tasks: list) -> list:
        results = []
        for i in range(0, len(tasks), self._batch_size):
            batch = await asyncio.gather(*tasks[i: i + self._batch_size], return_exceptions=True)
            results.extend(batch)
        return results

    # ------------------------------------------------------------------
    # 메인 진입점
    # ------------------------------------------------------------------

    async def process(
        self, chunks: list[DocChunk], documents: DoclingDocument, **kwargs
    ) -> list[DocChunk]:
        if not self._url or not self._model:
            _log.warning("enhanced_image_description 비활성: url/model 설정이 비어있습니다.")
            return chunks

        subject: str = kwargs.get("subject") or kwargs.get("external_subject") or ""

        tasks: list = []
        task_chunk_idxs: list[int] = []

        for chunk_idx, chunk in enumerate(chunks):
            doc_items = getattr(chunk.meta, "doc_items", None) or []
            picture_items = [item for item in doc_items if isinstance(item, PictureItem)]

            if len(picture_items) != 1:
                continue

            tasks.append(self._process_chunk(chunk_idx, picture_items[0], subject))
            task_chunk_idxs.append(chunk_idx)

        _log.info("enhanced_image_description: scheduled=%s total_chunks=%s", len(tasks), len(chunks))

        results = await self._run_in_batches(tasks)

        for item in results:
            if isinstance(item, Exception):
                _log.warning("enhanced_image_description task exception=%r", item)
                continue
            chunk_idx, description = item
            if not description:
                continue
            chunks[chunk_idx].text = description
            _log.debug("enhanced_image_description: chunk=%s replaced chars=%s", chunk_idx, len(description))

        return chunks
