"""
TableDescriptionPostprocessor:
청킹 후 TableItem 청크를 찾아 LLM으로 한국어 한 줄 설명을 생성하고
chunk.text 앞에 추가한다.

config 예시 (yaml 인라인):
    postprocessors:
      - table_description:
          url: "https://..."
          api_key: null
          model: "google/gemini-2.0-flash"
          max_tokens: 500
          temperature: 0.0
          timeout: 60
          batch_size: 5
          max_table_chars: 3000
          system_prompt: |
            당신은 문서 이해 전문가입니다.
          prompt: |
            다음 표의 핵심 내용을 한국어로 한 줄로 설명하세요.
            문서 주제: {subject}

            {table_text}
"""

from __future__ import annotations

import asyncio
import logging

import httpx
from docling_core.transforms.chunker import DocChunk
from docling_core.types import DoclingDocument
from docling_core.types.doc import TableItem

_log = logging.getLogger(__name__)

_DEFAULT_SYSTEM_PROMPT = (
    "당신은 문서 이해 전문가입니다. "
    "주어진 표를 분석하여 핵심 내용을 한국어로 한 줄(50자 이내)로 설명하세요. "
    "설명 문장만 출력하고 다른 내용은 출력하지 마세요."
)
_DEFAULT_PROMPT = "다음 표의 핵심 내용을 한국어로 한 줄(50자 이내)로 설명하세요.\n\n{table_text}"


class TableDescriptionPostprocessor:
    """
    TableItem 청크에 대해 LLM으로 한국어 한 줄 설명을 생성하고
    chunk.text 앞에 추가한다.

    - 청크 내 TableItem이 없으면 건너뜀
    - 청크에 여러 TableItem이 있으면 마크다운을 이어붙여 하나의 설명 생성
    - LLM 실패 시 해당 청크는 원본 유지
    """

    def __init__(
        self,
        url: str = "",
        api_key: str = "",
        model: str = "",
        max_tokens: int = 500,
        temperature: float = 0.0,
        timeout: int = 60,
        batch_size: int = 5,
        max_table_chars: int = 3000,
        system_prompt: str = "",
        prompt: str = "",
    ):
        self._url = url
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._timeout = timeout
        self._batch_size = batch_size
        self._max_table_chars = max_table_chars
        self._system_prompt = system_prompt.strip() or _DEFAULT_SYSTEM_PROMPT
        self._prompt_template = prompt.strip() or _DEFAULT_PROMPT
        self._headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"

    # ------------------------------------------------------------------
    # 테이블 텍스트 추출
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_table_markdown(item: TableItem, document: DoclingDocument) -> str:
        """TableItem → 마크다운 텍스트. 실패 시 셀 텍스트로 폴백."""
        try:
            md = item.export_to_markdown(doc=document)
            if md and md.strip():
                return md.strip()
        except Exception:
            pass
        try:
            if item.data and item.data.table_cells:
                parts = [
                    c.text.strip()
                    for c in item.data.table_cells
                    if (getattr(c, "text", "") or "").strip()
                ]
                if parts:
                    return " ".join(parts)
        except Exception:
            pass
        return getattr(item, "text", "") or ""

    # ------------------------------------------------------------------
    # LLM 호출
    # ------------------------------------------------------------------

    async def _call_llm(self, table_text: str, subject: str = "") -> str:
        prompt = self._prompt_template.format(
            table_text=table_text,
            subject=subject or "",
        )
        payload = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
            "messages": [
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": prompt},
            ],
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(self._url, json=payload, headers=self._headers)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()

    # ------------------------------------------------------------------
    # 단일 청크 처리
    # ------------------------------------------------------------------

    async def _process_chunk(
        self,
        chunk_idx: int,
        table_items: list[TableItem],
        document: DoclingDocument,
        subject: str,
    ) -> tuple[int, str | None]:
        table_parts = [
            self._extract_table_markdown(item, document) for item in table_items
        ]
        table_parts = [t for t in table_parts if t]
        if not table_parts:
            return chunk_idx, None

        combined = "\n\n".join(table_parts)
        if len(combined) > self._max_table_chars:
            combined = combined[: self._max_table_chars]

        try:
            description = await self._call_llm(combined, subject=subject)
            return chunk_idx, description
        except Exception as e:
            _log.warning(
                "table_description 실패 chunk=%s error=%s", chunk_idx, e
            )
            return chunk_idx, None

    # ------------------------------------------------------------------
    # 배치 처리
    # ------------------------------------------------------------------

    async def _run_in_batches(self, tasks: list) -> list:
        results = []
        for i in range(0, len(tasks), self._batch_size):
            batch = await asyncio.gather(
                *tasks[i : i + self._batch_size], return_exceptions=True
            )
            results.extend(batch)
        return results

    # ------------------------------------------------------------------
    # 메인 진입점
    # ------------------------------------------------------------------

    async def process(
        self, chunks: list[DocChunk], documents: DoclingDocument, **kwargs
    ) -> list[DocChunk]:
        if not self._url or not self._model:
            _log.warning("table_description 비활성: url/model 설정이 비어있습니다.")
            return chunks

        ctx = kwargs.get("_enrichment_context") or {}
        subject: str = (
            kwargs.get("subject")
            or kwargs.get("external_subject")
            or ctx.get("subject", "")
        )

        tasks: list = []
        for chunk_idx, chunk in enumerate(chunks):
            doc_items = getattr(chunk.meta, "doc_items", None) or []
            table_items = [item for item in doc_items if isinstance(item, TableItem)]
            if not table_items:
                continue
            tasks.append(
                self._process_chunk(chunk_idx, table_items, documents, subject)
            )

        _log.info(
            "table_description: scheduled=%s total_chunks=%s",
            len(tasks),
            len(chunks),
        )

        if not tasks:
            return chunks

        results = await self._run_in_batches(tasks)

        for item in results:
            if isinstance(item, Exception):
                _log.warning("table_description task exception=%r", item)
                continue
            chunk_idx, description = item
            if not description:
                continue
            chunks[chunk_idx].text = description + "\n" + chunks[chunk_idx].text
            _log.debug(
                "table_description: chunk=%s description=%r",
                chunk_idx,
                description,
            )

        return chunks
