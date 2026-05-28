from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Optional

from docling.utils.api_image_request import api_image_request
from docling_core.types import DoclingDocument
from docling_core.types.doc import (
    DescriptionAnnotation,
    DocItem,
    PictureItem,
)
from docling_core.types.doc.document import ContentLayer

from .base_enricher import BaseEnricher

_log = logging.getLogger(__name__)

_DEFAULT_IMAGE_DESCRIPTION_PROMPT_TEMPLATE = (
    "문서의 일부 이미지를 설명해줘. "
    "아래 문맥을 참고해서 핵심 정보를 2~4문장으로 간결하게 작성해줘.\n\n"
    "[앞 문맥]\n{before_context}\n\n"
    "[캡션]\n{caption}\n\n"
    "[뒤 문맥]\n{after_context}\n\n"
    "요구사항:\n"
    "1) 추측은 최소화하고 이미지에서 확인 가능한 사실 중심으로 작성\n"
    "2) 문서 문맥과의 연결점을 포함\n"
    "3) 한국어로 작성"
)


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class ImageDescriptionOptions:
    enabled: bool = False
    api_url: str = ""
    api_key: str = ""
    model: str = "model"
    timeout: float = 360.0
    concurrency: int = 16
    before_items: int = 3
    after_items: int = 2
    max_context_chars: int = 1500
    include_caption: bool = True
    include_section_header: bool = True
    same_page_first: bool = True
    provenance: str = "contextual_image_description"
    prompt_template: str = _DEFAULT_IMAGE_DESCRIPTION_PROMPT_TEMPLATE
    headers: dict[str, str] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_config(
        cls,
        *,
        image_desc_cfg: dict,
        fallback_api_url: str,
        fallback_api_key: str,
        fallback_model: str,
    ) -> "ImageDescriptionOptions":
        image_desc_cfg = _as_dict(image_desc_cfg)

        def _parse_optional_bool(value: Any, key: str) -> Optional[bool]:
            if value is None:
                return None
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return bool(value)
            if isinstance(value, str):
                text = value.strip().lower()
                if text in {"1", "true", "yes", "y", "on"}:
                    return True
                if text in {"0", "false", "no", "n", "off"}:
                    return False
            _log.warning(
                f"[ImageDescriptionOptions] Invalid bool value for '{key}': {value!r}. Fallback to default."
            )
            return None

        def _parse_optional_int(value: Any, key: str) -> Optional[int]:
            if value is None or value == "":
                return None
            try:
                return int(value)
            except (TypeError, ValueError):
                _log.warning(
                    f"[ImageDescriptionOptions] Invalid int value for '{key}': {value!r}. Fallback to default."
                )
                return None

        def _parse_optional_float(value: Any, key: str) -> Optional[float]:
            if value is None or value == "":
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                _log.warning(
                    f"[ImageDescriptionOptions] Invalid float value for '{key}': {value!r}. Fallback to default."
                )
                return None

        enabled = _parse_optional_bool(image_desc_cfg.get("enabled"), "enabled")
        timeout = _parse_optional_float(image_desc_cfg.get("timeout"), "timeout")
        concurrency = _parse_optional_int(image_desc_cfg.get("concurrency"), "concurrency")
        before_items = _parse_optional_int(image_desc_cfg.get("before_items"), "before_items")
        after_items = _parse_optional_int(image_desc_cfg.get("after_items"), "after_items")
        max_context_chars = _parse_optional_int(
            image_desc_cfg.get("max_context_chars"), "max_context_chars"
        )
        include_caption = _parse_optional_bool(image_desc_cfg.get("include_caption"), "include_caption")
        include_section_header = _parse_optional_bool(
            image_desc_cfg.get("include_section_header"), "include_section_header"
        )
        same_page_first = _parse_optional_bool(image_desc_cfg.get("same_page_first"), "same_page_first")

        timeout = 360.0 if timeout is None or timeout <= 0 else timeout
        if concurrency is None or concurrency <= 0:
            concurrency = 4
        if before_items is None or before_items < 0:
            before_items = 3
        if after_items is None or after_items < 0:
            after_items = 2
        if max_context_chars is None or max_context_chars <= 0:
            max_context_chars = 1500

        return cls(
            enabled=False if enabled is None else enabled,
            api_url=str(image_desc_cfg.get("api_url") or fallback_api_url or "").strip(),
            api_key=str(image_desc_cfg.get("api_key") or fallback_api_key or "").strip(),
            model=str(image_desc_cfg.get("model") or fallback_model or "model").strip(),
            timeout=timeout,
            concurrency=concurrency,
            before_items=before_items,
            after_items=after_items,
            max_context_chars=max_context_chars,
            include_caption=True if include_caption is None else include_caption,
            include_section_header=True if include_section_header is None else include_section_header,
            same_page_first=True if same_page_first is None else same_page_first,
            provenance=str(
                image_desc_cfg.get("provenance", "contextual_image_description")
            ).strip()
            or "contextual_image_description",
            prompt_template=str(
                image_desc_cfg.get("prompt_template", _DEFAULT_IMAGE_DESCRIPTION_PROMPT_TEMPLATE)
            ),
            headers=_as_dict(image_desc_cfg.get("headers")),
            params=_as_dict(image_desc_cfg.get("params")),
        )

    @classmethod
    def from_legacy_processor(cls, processor: Any) -> "ImageDescriptionOptions":
        def _safe_int(value: Any, default: int, min_value: int) -> int:
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                return default
            return parsed if parsed >= min_value else default

        def _safe_float(value: Any, default: float, min_value: float) -> float:
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                return default
            return parsed if parsed >= min_value else default

        return cls(
            enabled=bool(getattr(processor, "image_description_enabled", False)),
            api_url=str(getattr(processor, "image_description_api_url", "") or "").strip(),
            api_key=str(getattr(processor, "image_description_api_key", "") or "").strip(),
            model=str(getattr(processor, "image_description_model", "model") or "model").strip(),
            timeout=_safe_float(getattr(processor, "image_description_timeout", 20.0), 20.0, 0.00001),
            concurrency=_safe_int(getattr(processor, "image_description_concurrency", 4), 4, 1),
            before_items=_safe_int(getattr(processor, "image_description_before_items", 3), 3, 0),
            after_items=_safe_int(getattr(processor, "image_description_after_items", 2), 2, 0),
            max_context_chars=_safe_int(
                getattr(processor, "image_description_max_context_chars", 1500), 1500, 1
            ),
            include_caption=bool(getattr(processor, "image_description_include_caption", True)),
            include_section_header=bool(
                getattr(processor, "image_description_include_section_header", True)
            ),
            same_page_first=bool(getattr(processor, "image_description_same_page_first", True)),
            provenance=str(
                getattr(processor, "image_description_provenance", "contextual_image_description")
                or "contextual_image_description"
            ).strip(),
            prompt_template=str(
                getattr(
                    processor,
                    "image_description_prompt_template",
                    _DEFAULT_IMAGE_DESCRIPTION_PROMPT_TEMPLATE,
                )
            ),
            headers=dict(getattr(processor, "image_description_headers", {}) or {}),
            params=dict(getattr(processor, "image_description_params", {}) or {}),
        )


class PictureDescriptionExtractor:
    @staticmethod
    def extract(item: PictureItem) -> str:
        for annotation in getattr(item, "annotations", []) or []:
            if not isinstance(annotation, DescriptionAnnotation):
                continue
            text = str(getattr(annotation, "text", "") or "").strip()
            if text:
                return text
        return ""


class ContextualImageDescriptionEnricher(BaseEnricher):
    """문서 문맥(앞뒤 텍스트, 섹션 헤더, 캡션)을 활용해 이미지 설명을 생성하는 enricher.

    YAML config 예시:
        enrichers:
          - contextual_image_description:
              enabled: true
              api_url: "http://..."
              api_key: "..."
              model: "gpt-4o"
              concurrency: 8
              before_items: 3
              after_items: 2
    """

    def __init__(
        self,
        options: ImageDescriptionOptions | None = None,
        enabled: bool = False,
        api_url: str = "",
        api_key: str = "",
        model: str = "model",
        timeout: float = 360.0,
        concurrency: int = 16,
        before_items: int = 3,
        after_items: int = 2,
        max_context_chars: int = 1500,
        include_caption: bool = True,
        include_section_header: bool = True,
        same_page_first: bool = True,
        provenance: str = "contextual_image_description",
        prompt_template: str = _DEFAULT_IMAGE_DESCRIPTION_PROMPT_TEMPLATE,
        headers: dict | None = None,
        params: dict | None = None,
    ):
        if options is not None:
            self.options = options
        else:
            self.options = ImageDescriptionOptions(
                enabled=enabled,
                api_url=api_url,
                api_key=api_key,
                model=model,
                timeout=timeout,
                concurrency=concurrency,
                before_items=before_items,
                after_items=after_items,
                max_context_chars=max_context_chars,
                include_caption=include_caption,
                include_section_header=include_section_header,
                same_page_first=same_page_first,
                provenance=provenance,
                prompt_template=prompt_template,
                headers=headers or {},
                params=params or {},
            )

    @staticmethod
    def _get_item_page_no(item: DocItem, default_page_no: int = 1) -> int:
        prov_list = getattr(item, "prov", None) or []
        if not prov_list:
            return default_page_no
        page_no = getattr(prov_list[0], "page_no", None)
        if isinstance(page_no, int) and page_no > 0:
            return page_no
        return default_page_no

    @staticmethod
    def _to_single_line(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    def _is_context_candidate(self, item: DocItem) -> bool:
        if isinstance(item, PictureItem):
            return False

        text = self._to_single_line(str(getattr(item, "text", "") or ""))
        if not text:
            return False

        label = getattr(item, "label", None)
        label_value = label.value if hasattr(label, "value") else str(label or "")
        if label_value in {"page_header", "page_footer"}:
            return False
        return True

    def _collect_neighbor_context(
        self,
        items: list[DocItem],
        picture_index: int,
        picture_page_no: int,
        max_items: int,
        direction: str,
    ) -> list[str]:
        if max_items <= 0:
            return []

        if direction == "before":
            scan_range = range(picture_index - 1, -1, -1)
        else:
            scan_range = range(picture_index + 1, len(items))

        sequential: list[str] = []
        same_page: list[str] = []
        cross_page: list[str] = []

        for idx in scan_range:
            candidate = items[idx]
            if not self._is_context_candidate(candidate):
                continue
            text = self._to_single_line(str(getattr(candidate, "text", "") or ""))
            if not text:
                continue

            if not self.options.same_page_first:
                sequential.append(text)
                if len(sequential) >= max_items:
                    break
                continue

            candidate_page_no = self._get_item_page_no(
                candidate, default_page_no=picture_page_no
            )
            if candidate_page_no == picture_page_no:
                same_page.append(text)
            else:
                cross_page.append(text)

            if len(same_page) + len(cross_page) >= max_items:
                break

        if self.options.same_page_first:
            if direction == "before":
                # 그룹 우선순위(same page -> cross page)는 유지하면서 문서 순서로 정렬
                same_page = list(reversed(same_page))
                cross_page = list(reversed(cross_page))
            selected = (same_page + cross_page)[:max_items]
        else:
            selected = sequential[:max_items]
            if direction == "before":
                # 앞 문맥은 문서 순서(먼저 나온 텍스트 → 최근 텍스트)로 정렬한다.
                selected.reverse()
        return selected

    def _collect_section_header_context(
        self,
        items: list[DocItem],
        picture_index: int,
    ) -> str:
        for idx in range(picture_index - 1, -1, -1):
            candidate = items[idx]
            label = getattr(candidate, "label", None)
            label_value = label.value if hasattr(label, "value") else str(label or "")
            if label_value in {"section_header", "title"}:
                text = self._to_single_line(str(getattr(candidate, "text", "") or ""))
                if text:
                    return text
        return ""

    def _truncate_context(self, text: str) -> str:
        if len(text) <= self.options.max_context_chars:
            return text
        return text[: self.options.max_context_chars].rstrip() + " ..."

    def _build_prompt(self, before_context: str, after_context: str, caption: str) -> str:
        safe_before = before_context or "-"
        safe_after = after_context or "-"
        safe_caption = caption or "-"
        try:
            prompt = self.options.prompt_template.format(
                before_context=safe_before,
                after_context=safe_after,
                caption=safe_caption,
            )
        except Exception as exc:
            _log.warning(
                f"[ContextualImageDescriptionEnricher] Invalid prompt_template, fallback to default: {exc}"
            )
            prompt = (
                "문맥을 참고해서 이미지를 설명해줘.\n\n"
                f"[앞 문맥]\n{safe_before}\n\n"
                f"[캡션]\n{safe_caption}\n\n"
                f"[뒤 문맥]\n{safe_after}"
            )
        return self._truncate_context(prompt)

    def _annotate_single_picture(
        self,
        document: DoclingDocument,
        picture_item: PictureItem,
        prompt: str,
    ) -> Optional[DescriptionAnnotation]:
        image = picture_item.get_image(document, prov_index=0)
        if image is None:
            return None

        headers = dict(self.options.headers)
        if self.options.api_key and "Authorization" not in headers:
            headers["Authorization"] = f"Bearer {self.options.api_key}"

        params = dict(self.options.params)
        if self.options.model and "model" not in params:
            params["model"] = self.options.model

        output = api_image_request(
            image=image,
            prompt=prompt,
            url=self.options.api_url,
            timeout=self.options.timeout,
            headers=headers,
            **params,
        )
        output_text = str(output or "").strip()
        if not output_text:
            return None
        return DescriptionAnnotation(
            text=output_text,
            provenance=self.options.provenance,
        )

    def _enrich_sync(self, document: DoclingDocument, **kwargs) -> DoclingDocument:
        if not self.options.enabled:
            return document

        stage_started_at = time.perf_counter()

        if not self.options.api_url:
            _log.warning(
                "[ContextualImageDescriptionEnricher] enabled=true but api_url is empty; skip"
            )
            return document

        items: list[DocItem] = [
            item
            for item, _ in document.iterate_items(
                included_content_layers={ContentLayer.BODY, ContentLayer.FURNITURE}
            )
        ]
        if not items:
            return document

        targets: list[tuple[int, PictureItem, str]] = []
        for idx, item in enumerate(items):
            if not isinstance(item, PictureItem):
                continue

            page_no = self._get_item_page_no(item, default_page_no=1)
            before_context_items = self._collect_neighbor_context(
                items=items,
                picture_index=idx,
                picture_page_no=page_no,
                max_items=self.options.before_items,
                direction="before",
            )
            after_context_items = self._collect_neighbor_context(
                items=items,
                picture_index=idx,
                picture_page_no=page_no,
                max_items=self.options.after_items,
                direction="after",
            )

            if self.options.include_section_header:
                section_header = self._collect_section_header_context(
                    items=items, picture_index=idx
                )
                if section_header:
                    before_context_items = [section_header] + before_context_items

            caption = ""
            if self.options.include_caption:
                try:
                    caption = self._to_single_line(item.caption_text(document))
                except Exception:
                    caption = ""

            before_context = "\n".join(before_context_items)
            after_context = "\n".join(after_context_items)
            prompt = self._build_prompt(
                before_context=before_context,
                after_context=after_context,
                caption=caption,
            )
            picture_seq = len(targets) + 1
            targets.append((picture_seq, item, prompt))

        if not targets:
            elapsed = time.perf_counter() - stage_started_at
            _log.info(
                f"[ContextualImageDescriptionEnricher] no picture target for image description; "
                f"elapsed={elapsed:.3f}s"
            )
            return document

        total_targets = len(targets)
        _log.info(
            f"[ContextualImageDescriptionEnricher] image description start: "
            f"targets={total_targets}, concurrency={self.options.concurrency}"
        )

        stats_lock = threading.Lock()
        success_count = 0
        failed_count = 0
        skipped_count = 0

        def _annotate_target(target: tuple[int, PictureItem, str]) -> None:
            nonlocal success_count, failed_count, skipped_count
            seq, pic, prompt = target
            picture_started_at = time.perf_counter()
            page_no = self._get_item_page_no(pic, default_page_no=1)
            try:
                annotation = self._annotate_single_picture(document, pic, prompt)
            except Exception as exc:
                elapsed = time.perf_counter() - picture_started_at
                with stats_lock:
                    failed_count += 1
                _log.warning(
                    f"[ContextualImageDescriptionEnricher] image description failed: "
                    f"seq={seq}, page={page_no}, elapsed={elapsed:.3f}s, error={exc}"
                )
                return
            if annotation is None:
                elapsed = time.perf_counter() - picture_started_at
                with stats_lock:
                    skipped_count += 1
                _log.debug(
                    f"[ContextualImageDescriptionEnricher] image description empty: "
                    f"seq={seq}, page={page_no}, elapsed={elapsed:.3f}s"
                )
                return
            pic.annotations = [
                ann
                for ann in pic.annotations
                if not (
                    isinstance(ann, DescriptionAnnotation)
                    and getattr(ann, "provenance", "") == self.options.provenance
                )
            ]
            pic.annotations.append(annotation)
            elapsed = time.perf_counter() - picture_started_at
            with stats_lock:
                success_count += 1
            _log.debug(
                f"[ContextualImageDescriptionEnricher] image description done: "
                f"seq={seq}, page={page_no}, elapsed={elapsed:.3f}s"
            )

        with ThreadPoolExecutor(max_workers=self.options.concurrency) as executor:
            list(executor.map(_annotate_target, targets))

        total_elapsed = time.perf_counter() - stage_started_at
        _log.info(
            f"[ContextualImageDescriptionEnricher] image description done: "
            f"targets={total_targets}, success={success_count}, skipped={skipped_count}, "
            f"failed={failed_count}, elapsed={total_elapsed:.3f}s"
        )

        return document

    async def enrich(self, document: DoclingDocument, **kwargs) -> DoclingDocument:
        return await asyncio.to_thread(self._enrich_sync, document, **kwargs)
