"""텍스트(HTML/Markdown) 표 설명을 독립 스테이지로 수행하는 실행기.

## 왜 필요한가

`table_text_description` 은 원래 문서형 custom_fields LLM 호출에 표를 얹어 호출 1회로
끝내는 기능이다. 그래서 연결(url/model)도 매칭된 custom_fields YAML 것을 빌려 썼는데,
그 결과 아래 문서들에서는 표 설명이 아예 만들어지지 않았다.

- 문서유형에 맞는 custom_fields 설정이 없는 문서
- 설정은 있지만 `extractor` 가 `llm`/`document_llm` 이 아닌 문서
  (`tabular_mapping`/`json_mapping`/`json_semantic` 은 enricher 자체가 생성되지 않는다)
- 설정은 있지만 그 YAML 에 url/model 이 비어 있는 문서

이 모듈은 `table_text_description` 블록이 **자기 LLM 연결을 가질 수 있게** 하고, 위 경우에
표 설명만 따로 만든다. 다른 enrichment 항목(`image_description`/`table_description` 등)이
이미 각자 url/api_key/model 을 갖는 것과 같은 모양이다.

## 자체 연결이 있으면 그쪽이 이긴다

`table_text_description` 에 url/model 이 있으면 **custom_fields 의 LLM 사용 여부와 무관하게**
이 실행기가 표 설명을 맡는다. 표 설명 품질을 custom_fields 설정(모델·thinking·프롬프트)에
묶어 두지 않기 위해서다 — 문서유형마다 다른 추출 설정에 표 설명이 딸려 다니면 같은 표가
문서유형에 따라 다른 모델로 설명된다.

자체 연결이 비어 있을 때만 예전처럼 문서형 custom_fields 호출에 얹는다(하위 호환). 그 둘도
아니면 이미지 기반 `table_description` 으로 폴백한다. 판정은 `apply_table_description_stage`
한 곳에 있고, 독립 실행이 표 설명을 가져간 요청에는 `_table_text_desc_owned` 를 남겨
custom_fields 융합 경로가 같은 표를 다시 설명하지 않게 한다.
"""
from __future__ import annotations

import inspect
import logging
import re
from dataclasses import replace
from typing import Any, Callable, Optional

from .table_description import TABLE_RETRIEVAL_LABEL
from .table_text_context import TableTextDescriptionOptions, TableTextTarget

_log = logging.getLogger(__name__)

# 표 설명 전용 system prompt. custom_fields 융합 경로는 문서유형 YAML 의 system_prompt 를
# 쓰지만, 독립 실행에는 그런 문서가 없으므로 표 설명 과제만 설명하는 기본값을 둔다.
DEFAULT_TABLE_TEXT_SYSTEM_PROMPT = (
    "너는 문서 표 분석 전문가다. 주어진 표와 주변 문맥을 읽고 요청한 JSON 만 정확하게 반환하라."
)


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}



# ── 텍스트 안의 표(레코드 경로) ────────────────────────────────────────────────
# json/tabular 매핑은 docling 문서를 만들지 않는다 — 표는 이미 레코드 본문 문자열 안의
# `<table>` 블록(또는 파이프 표)이다. 그래서 문서 대신 텍스트에서 표를 찾아 같은 프롬프트에
# 태우고, 설명은 annotation 이 아니라 표 바로 앞의 `[표 검색 설명]` 블록으로 넣는다
# (청커의 docling 경로가 붙이는 접두와 같은 모양이라 최종 청크가 두 경로에서 동일하다).
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.*\S)\s*$")


def find_table_blocks(text: str) -> list[tuple[int, int, str]]:
    """텍스트 안의 표 블록을 (시작, 끝, 형식) 으로 문서 순서대로 찾는다.

    HTML 표를 먼저 잡고, 그 바깥에서만 파이프 표를 찾는다 — `table_format` 이 html 이어도
    본문에 markdown 표가 섞여 있을 수 있어 형식을 설정이 아니라 실제 마크업으로 판정한다.

    검출은 청킹 경로와 같은 공용 모듈(`facade/chunking/table_blocks`) 한 벌을 쓴다. 표
    경계 판정이 두 벌이면 설명을 넣은 자리와 청크를 끊는 자리가 어긋난다.
    """
    from genon.preprocessor.facade.chunking import table_blocks as tbk

    return [(block.start, block.end, block.kind) for block in tbk.find_blocks(text)]


def _context_before(text: str, start: int, count: int, limit: int) -> str:
    if not count:
        return ""
    lines = [line.strip() for line in text[:start].splitlines() if line.strip()]
    return "\n".join(lines[-count:])[:limit]


def _context_after(text: str, end: int, count: int, limit: int) -> str:
    if not count:
        return ""
    lines = [line.strip() for line in text[end:].splitlines() if line.strip()]
    return "\n".join(lines[:count])[:limit]


def _nearest_heading(text: str, start: int) -> str:
    for line in reversed(text[:start].splitlines()):
        matched = _HEADING_RE.match(line)
        if matched:
            return matched.group(1)
    return ""


def build_text_table_targets(
    text: str, options: TableTextDescriptionOptions
) -> list[TableTextTarget]:
    """텍스트에서 찾은 표마다 LLM 입력 target 을 만든다(docling 문서 없이).

    `table_item` 은 문서 경로 전용이라 여기서는 None 이다 — 프롬프트 렌더링
    (`render_table_targets`)은 이 필드를 쓰지 않는다.
    """
    targets: list[TableTextTarget] = []
    for start, end, fmt in find_table_blocks(text):
        targets.append(TableTextTarget(
            table_id=f"table_{len(targets) + 1:04d}",
            table_item=None,
            page_no=1,
            section_header=_nearest_heading(text, start),
            caption="",
            before_context=_context_before(
                text, start, options.before_items, options.max_context_chars
            ),
            table_text=text[start:end].strip(),
            after_context=_context_after(
                text, end, options.after_items, options.max_context_chars
            ),
            input_format=fmt,
        ))
    return targets


def format_retrieval_block(entry: dict, options: TableTextDescriptionOptions) -> str:
    """LLM 응답 한 건을 청크 본문에 넣을 `[표 검색 설명]` 블록으로 만든다.

    docling 경로의 `TableDescriptionExtractor.retrieval_text` 와 같은 구성(문맥 → 핵심 사실
    → 선택적 검색어)이어야 두 경로의 청크가 같은 모양이 된다.
    """
    context = re.sub(r"\s+", " ", str(entry.get("retrieval_context") or "")).strip()
    context = context[:options.retrieval_context_max_chars]
    if not context:
        return ""
    lines = [context]
    facts = _clean_list(entry.get("key_facts"), options.key_fact_limit, options.key_fact_max_chars)
    if facts:
        lines.append("핵심 사실: " + " | ".join(facts))
    if options.include_search_terms:
        terms = _clean_list(entry.get("search_terms"), options.search_terms_limit)
        if terms:
            lines.append("검색어: " + ", ".join(terms))
    return f"{TABLE_RETRIEVAL_LABEL}\n" + "\n".join(lines) + "\n"


def _clean_list(value: Any, limit: int, max_chars: int | None = None) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = re.sub(r"\s+", " ", str(item or "")).strip()
        if not text or text in result:
            continue
        result.append(text[:max_chars] if max_chars else text)
        if len(result) >= limit:
            break
    return result


class TableTextDescriptionEnricher:
    """`table_text_description` 블록의 자체 LLM 연결로 표 설명만 만드는 실행기.

    본체는 `CustomFieldsEnricher` 를 output_fields 없이 표 전용으로 세워 재사용한다 —
    프롬프트 구성·예산 판정·배치 분할·응답 파싱·annotation 부착이 융합 경로와 한 벌이어야
    두 경로의 산출물이 어긋나지 않는다.
    """

    def __init__(self, cfg: dict | None = None):
        self._cfg = dict(cfg or {})
        self._options = TableTextDescriptionOptions.from_config(self._cfg)
        self._runner: Any = None

    @property
    def options(self) -> TableTextDescriptionOptions:
        return self._options

    @property
    def is_configured(self) -> bool:
        """자체 LLM 연결이 채워져 있는지. 비어 있으면 융합 경로에만 의존한다."""
        return bool(self._cfg.get("url") and self._cfg.get("model"))

    def wants(self, **kwargs: Any) -> bool:
        """이번 요청에서 독립 표 설명을 수행할지.

        판정 축은 융합 경로(`CustomFieldsEnricher.wants_table_descriptions`)와 같다 —
        런타임 플래그 우선, 프롬프트 없으면 하지 않음, `prefer_image` 면 이미지 경로에 양보.
        여기에 "자체 연결이 있는가"가 하나 더 붙는다.
        """
        if not self.is_configured:
            return False
        if self._options.conflict_policy == "prefer_image":
            return False
        runtime = kwargs.get("table_text_desc")
        wanted = self._options.enabled if runtime is None else _as_bool(runtime)
        if not wanted:
            return False
        if not self._prompt_available():
            _log.warning(
                "표 설명을 요청했지만 table_text_description.prompt_template_file 설정이 없어 건너뜁니다."
            )
            return False
        return True

    def _prompt_available(self) -> bool:
        return bool(
            self._cfg.get("prompt_template_file")
            or self._cfg.get("prompt_file")
            or self._cfg.get("prompt_template")
            or self._cfg.get("prompt")
        )

    def _get_runner(self) -> Any:
        """표 전용 CustomFieldsEnricher(lazy). 추출 필드가 없어 표 설명만 만든다."""
        if self._runner is not None:
            return self._runner
        from .custom_fields_enricher import CustomFieldsEnricher

        cfg = self._cfg
        self._runner = CustomFieldsEnricher(
            api_key=str(cfg.get("api_key") or ""),
            url=str(cfg.get("url") or ""),
            model=str(cfg.get("model") or ""),
            max_tokens=int(cfg.get("max_tokens") or 4000),
            temperature=float(cfg.get("temperature") or 0.0),
            timeout=int(cfg.get("timeout") or 300),
            system_prompt=str(cfg.get("system_prompt") or DEFAULT_TABLE_TEXT_SYSTEM_PROMPT),
            thinking=cfg.get("thinking"),
            thinking_dialect=str(cfg.get("thinking_dialect") or "standard"),
            resource_path=cfg.get("resource_path"),
            output_fields=[],
            table_text_description=cfg,
        )
        return self._runner

    async def enrich(self, document: Any, **kwargs: Any) -> Any:
        """문서의 텍스트 표에 설명 annotation 을 붙인다. 실패해도 문서는 그대로 돌려준다."""
        await self._get_runner().describe_tables_only(document, **kwargs)
        return document

    async def describe_texts(self, texts: list[str], **kwargs: Any) -> list[str]:
        """레코드 본문 문자열들의 표 앞에 `[표 검색 설명]` 블록을 넣어 돌려준다.

        docling 문서가 없는 경로(json/tabular 매핑)용이다. 여러 레코드의 표를 한 번에 모아
        예산이 허락하는 만큼 묶어 호출하므로, 레코드가 많아도 호출 수는 표 총량에만 비례한다.
        실패하면 원문을 그대로 돌려준다 — 표 설명은 부가 기능이고 본문 적재를 막으면 안 된다.
        """
        options = self._options
        plans: list[tuple[int, list[TableTextTarget]]] = []
        all_targets: list[TableTextTarget] = []
        for index, text in enumerate(texts):
            targets = build_text_table_targets(text or "", options)
            if not targets:
                continue
            # table_id 는 전체에서 유일해야 응답을 되짚을 수 있다.
            renumbered = [
                replace(target, table_id=f"table_{len(all_targets) + offset + 1:04d}")
                for offset, target in enumerate(targets)
            ]
            all_targets.extend(renumbered)
            plans.append((index, renumbered))
        if not all_targets:
            return list(texts)

        try:
            described = await self._get_runner().describe_table_targets(all_targets, **kwargs)
        except Exception as exc:
            _log.warning(f"[table_text_description] 레코드 표 설명 실패(원문 유지): {exc}")
            return list(texts)

        result = list(texts)
        described_count = 0
        for index, targets in plans:
            text = result[index] or ""
            # 뒤에서부터 넣어야 앞쪽 오프셋이 밀리지 않는다.
            spans = find_table_blocks(text)
            for target, (start, _end, _fmt) in reversed(list(zip(targets, spans))):
                block = format_retrieval_block(described.get(target.table_id) or {}, options)
                if not block:
                    continue
                text = f"{text[:start]}{block}{text[start:]}"
                described_count += 1
            result[index] = text
        _log.info(
            f"[table_text_description] 레코드 표 {len(all_targets)}건 중 {described_count}건에 설명 부착"
        )
        return result


async def apply_table_description_stage(
    document: Any,
    *,
    custom_fields_enrichers: Any,
    standalone: Optional[TableTextDescriptionEnricher],
    run_image_stage: Callable[..., Any],
    handle_error: Callable[[Exception, str], None],
    kwargs: dict,
) -> Any:
    """표 설명 스테이지 하나를 결정해 실행한다(독립 → 융합 → 이미지 순).

    facade 3종이 같은 판정을 복제하지 않도록 여기 한 벌만 둔다. 인자로 받는 두 콜러블은
    facade 마다 다른 것(자기 이미지 표 설명 호출, 자기 에러 정책)뿐이다.

    `kwargs` 를 `**` 로 풀지 않고 dict 그대로 받는 이유는 독립 실행이 표 설명을 가져갔음을
    `_table_text_desc_owned` 로 남겨야 하기 때문이다 — 이 스테이지는 custom_fields 스테이지보다
    먼저 돌므로, 뒤이어 도는 custom_fields 가 그 표를 다시 설명하지 않는다.
    """
    if standalone is not None and standalone.wants(**kwargs):
        kwargs["_table_text_desc_owned"] = True
        if standalone.options.conflict_policy == "error" and kwargs.get("table_desc"):
            handle_error(
                ValueError("텍스트 표 설명과 이미지 표 설명이 동시에 활성화되었습니다."),
                "table_description",
            )
            return document
        try:
            return await standalone.enrich(document, **kwargs)
        except Exception as exc:
            handle_error(exc, "table_text_description")
            return document

    text_table_enricher = next((
        enricher for enricher in (custom_fields_enrichers or [])
        if enricher.wants_table_descriptions(**kwargs)
    ), None)

    if text_table_enricher is not None:
        # 융합 경로 — 실제 호출은 custom_fields 추출과 함께 일어나므로 여기서는 아무것도 하지
        # 않는다. prefer_image 는 wants_table_descriptions 안에서 이미 False 로 걸러진다.
        if (
            text_table_enricher.table_description_conflict_policy == "error"
            and kwargs.get("table_desc")
        ):
            handle_error(
                ValueError("텍스트 표 설명과 이미지 표 설명이 동시에 활성화되었습니다."),
                "table_description",
            )
        return document

    try:
        result = run_image_stage(document, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result if result is not None else document
    except Exception as exc:
        handle_error(exc, "table_description")
        return document
