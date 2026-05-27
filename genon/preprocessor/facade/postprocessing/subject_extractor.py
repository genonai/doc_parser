"""
SubjectExtractor:
문서 전문 텍스트 → LLM → subject / category / title 추출.
결과는 _enrichment_context["subject"], ["subject_metadata"] 에 저장되어
downstream postprocessor(enhanced_image_description 등)에서 참조한다.

※ yaml에서 enhanced_image_description 보다 앞에 위치해야 한다.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import httpx
from docling_core.transforms.chunker import DocChunk
from docling_core.types import DoclingDocument

_log = logging.getLogger(__name__)


class SubjectExtractor:
    """문서 주제(subject) / 카테고리 / 제목 추출 postprocessor.

    chunks 는 건드리지 않고 _enrichment_context 에만 결과를 저장한다:
        _enrichment_context["subject"]          → str
        _enrichment_context["subject_metadata"] → {"subject": ..., "category": ..., "title": ...}

    yaml 에서 반드시 enhanced_image_description 보다 앞에 위치해야 한다.

    config 예시:
        postprocessors:
          - subject_extractor:
              url: "https://..."
              api_key: null
              model: "google/gemini-2.0-flash"
              max_chars: 12000
              max_tokens: 2000
              temperature: 0.0
              timeout: 60
    """

    def __init__(
        self,
        url: str = "",
        api_key: str = "",
        model: str = "",
        max_chars: int = 12000,
        max_tokens: int = 2000,
        temperature: float = 0.0,
        timeout: int = 60,
        system_prompt: str = "",
    ):
        self._url = url
        self._model = model
        self._max_chars = max_chars
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._timeout = timeout
        self._system_prompt = system_prompt.strip()
        self._headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"

    # ------------------------------------------------------------------
    # 텍스트 추출
    # ------------------------------------------------------------------

    def _extract_text(self, document: DoclingDocument, file_path: str) -> str:
        if file_path.lower().endswith(".pdf"):
            return self._extract_text_fitz(file_path)
        text = document.export_to_text()
        return text[:self._max_chars] if text else ""

    def _extract_text_fitz(self, file_path: str) -> str:
        try:
            import fitz
        except ImportError:
            _log.warning("[subject] fitz 없음, export_to_text() 폴백")
            return ""

        full_text = ""
        try:
            with fitz.open(file_path) as docs:
                for page in docs:
                    try:
                        text = page.get_text("text")
                        if text:
                            full_text += text + "\n"
                            if len(full_text) >= self._max_chars:
                                return full_text[:self._max_chars]
                    except Exception as e:
                        _log.warning("[subject] page text 실패: %s", e)
        except Exception as e:
            _log.warning("[subject] fitz open 실패: %s", e)
        return full_text

    # ------------------------------------------------------------------
    # LLM 호출
    # ------------------------------------------------------------------

    async def _call_llm(self, text: str) -> dict | None:
        payload = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
            "messages": [
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": text},
            ],
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(self._url, json=payload, headers=self._headers)
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]

        if isinstance(content, dict):
            return content
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", content, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group())
                except json.JSONDecodeError:
                    pass
        return None

    # ------------------------------------------------------------------
    # 메인 진입점
    # ------------------------------------------------------------------

    async def process(
        self, chunks: list[DocChunk], documents: DoclingDocument, **kwargs
    ) -> list[DocChunk]:
        if not self._url or not self._model:
            _log.warning("[subject] 비활성: url/model 설정이 비어있습니다.")
            return chunks

        file_path: str = kwargs.get("file_path", "")
        external_subject: str = kwargs.get("subject") or kwargs.get("external_subject") or ""
        fallback_title = Path(file_path).stem if file_path else ""
        fallback = {
            "subject": external_subject or fallback_title,
            "category": "기타",
            "title": fallback_title,
        }

        context = kwargs.get("_enrichment_context")
        if not isinstance(context, dict):
            context = None

        if external_subject:
            result = {**fallback, "subject": external_subject}
            _log.info("[subject] external_subject 사용: chars=%s", len(external_subject))
        else:
            text = self._extract_text(documents, file_path)
            if not text.strip():
                _log.warning("[subject] 텍스트 없음, fallback 사용")
                result = fallback
            else:
                try:
                    output = await self._call_llm(text)
                except Exception as e:
                    _log.warning("[subject] LLM 호출 실패: %s", e)
                    output = None

                if not isinstance(output, dict):
                    result = fallback
                else:
                    subject = str(output.get("subject") or "").strip() or fallback["subject"]
                    category = str(output.get("category") or "").strip()
                    title = str(output.get("title") or "").strip()
                    if category not in {"산출방법서", "약관", "기타"}:
                        category = "기타"
                    result = {"subject": subject, "category": category, "title": title}

        _log.info(
            "[subject] subject_chars=%s category=%s title=%r",
            len(result["subject"]), result["category"], result["title"],
        )

        if context is not None:
            context["subject"] = result["subject"]
            context["subject_metadata"] = result

        return chunks
