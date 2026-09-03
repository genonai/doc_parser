"""문서 단위 custom_fields 의 Markdown YAML front matter 처리.

## 왜 필요한가

docling 의 Markdown 백엔드는 `---` 로 감싼 front matter 를 **일반 본문 TextItem** 으로 읽는다.
상품설명서(md) 원천은 선두에 file/document_type/source_file/source_pages/author/created_at/
conversion_note 7줄을 갖는데, 그대로 두면 그 7줄만으로 이루어진 청크가 하나 생기고(실측 283자)
검색 노이즈가 된다. 이 모듈은 docling 변환 **전에** front matter 를 분리해

  - `metadata_fields`      : 어떤 키를 청크 metadata 로 승격할지
  - `exclude_text_fields`  : 어떤 키를 청크 텍스트에서 뺄지

를 **서로 독립적으로** 선택하게 한다. 본문에서 뺀 front matter 도 LLM 추출 프롬프트에는
계속 실어(`prompt_prefix`) 추출 근거를 잃지 않는다.

## 설정 위치

형제 모듈 `json_text.JsonTextSpec` 와 같은 자리다 — `custom_fields` 항목 하위 블록이며
`custom_fields_enricher._NON_ENRICHER_KEYS` 에 등록되어 enricher 생성자로 새지 않는다.
기본값은 `config_file` 이 가리키는 doc_type yaml 의 `markdown:` 블록에 두고, 상위
`custom_fields` 항목에 같은 블록을 쓰면 재귀 병합으로 덮어쓴다.

`markdown:` 블록의 해석(`resolve_markdown_cfg`)과 하위 블록 빌더를 이 모듈이 소유한다 —
`front_matter` 외에 `text_fence`(펜스 본문을 단락으로 복원, 변환 로직은
`converters.md_text_fence`)도 같은 자리에서 컴파일한다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from genon.preprocessor.converters.md_text_fence import MarkdownTextFenceSpec

from .custom_fields_enricher import (
    DOCUMENT_CUSTOM_FIELD_EXTRACTORS,
    custom_fields_extractor,
    load_custom_fields_config,
    normalize_doc_type,
    normalize_doc_types,
)

_log = logging.getLogger(__name__)

_POLICIES = {"ignore", "warn", "error"}
_DEFAULT_MAX_BYTES = 64 * 1024

# GenOSVectorMeta/청크 조립이 소유한 이름들. 조용히 받아주면 원천 값이 버려지거나
# 청크 구조 필드를 덮어쓴다(tabular_custom_fields.validate_target_field_names 와 같은 취지).
#
# `created_date` 는 **일부러 뺐다** — 청커의 metadata field_transform
# (field_transforms.DEFAULT_METADATA_FIELD_TRANSFORMS: source=created_date, type=date_int)이
# 이 키를 소비해 YYYYMMDD 정수 벡터 필드로 바꾼다. front matter 의 `created_at` 을 여기로
# 매핑하는 것이 정상 경로다. 문자열 원본은 transform 의 consumed_keys 와 청커 예약키 제거로
# passthrough 되지 않으므로 타입 검증에 걸리지 않는다.
_RESERVED_TARGETS = {
    "text", "n_char", "n_word", "n_line", "e_page", "i_page",
    "i_chunk_on_page", "n_chunk_of_page", "i_chunk_on_doc", "n_chunk_of_doc",
    "n_page", "reg_date", "chunk_bboxes", "media_files", "title", "appendix",
    "file_path", "metadata", "guardrail_categories", "doc_type",
}


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """중복 매핑 키를 거부하는 SafeLoader.

    yaml 기본 동작은 뒤에 온 값으로 조용히 덮어쓴다 — 원천 front matter 에 같은 키가 두 번
    있으면 어느 값이 metadata 가 될지 예측할 수 없으므로 오류로 드러낸다.
    """

    def construct_mapping(self, node, deep=False):
        mapping = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in mapping:
                raise ValueError(f"Markdown front matter 중복 키: {key}")
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def _normalize_value(
    value: Any,
    *,
    _active: set[int] | None = None,
    _memo: dict[int, Any] | None = None,
    _depth: int = 0,
) -> Any:
    """YAML 고유 타입을 JSON/docling 이 안전하게 실을 수 있는 값으로 재귀 변환한다.

    date/datetime 은 문자열(ISO)로 내린다 — KeyValueItem 은 문자열만 싣고, 청커의
    date_int transform 도 `2026-01-12` 표기를 그대로 받는다.
    """
    if _depth > 32:
        raise ValueError("Markdown front matter 중첩 깊이가 32를 초과했습니다.")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (dict, list, tuple)):
        active = _active if _active is not None else set()
        memo = _memo if _memo is not None else {}
        value_id = id(value)
        if value_id in active:
            raise ValueError("Markdown front matter에 순환 YAML alias가 있습니다.")
        if value_id in memo:
            return memo[value_id]
        active.add(value_id)
        try:
            if isinstance(value, dict):
                normalized = {
                    str(key): _normalize_value(
                        item, _active=active, _memo=memo, _depth=_depth + 1
                    )
                    for key, item in value.items()
                }
            else:
                normalized = [
                    _normalize_value(
                        item, _active=active, _memo=memo, _depth=_depth + 1
                    )
                    for item in value
                ]
        finally:
            active.remove(value_id)
        memo[value_id] = normalized
        return normalized
    return str(value)


def _policy(value: Any, name: str, default: str) -> str:
    policy = str(value or default).strip().lower()
    if policy not in _POLICIES:
        raise ValueError(
            f"custom_fields.markdown.front_matter.{name}는 "
            f"{sorted(_POLICIES)} 중 하나여야 합니다: {value!r}"
        )
    return policy


def _metadata_field_map(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if isinstance(value, list):
        result = {}
        for item in value:
            key = str(item or "").strip()
            if not key:
                raise ValueError("front_matter.metadata_fields에 빈 필드명이 있습니다.")
            result[key] = key
        return result
    if isinstance(value, dict):
        result = {}
        for source, target in value.items():
            source_name = str(source or "").strip()
            target_name = str(target or "").strip()
            if not source_name or not target_name:
                raise ValueError("front_matter.metadata_fields의 source/target은 비어 있을 수 없습니다.")
            if target_name in result.values():
                raise ValueError(
                    f"front_matter.metadata_fields 대상 필드가 중복됩니다: {target_name}"
                )
            result[source_name] = target_name
        return result
    raise ValueError("front_matter.metadata_fields는 list 또는 mapping이어야 합니다.")


def _field_list(value: Any, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"front_matter.{name}는 list여야 합니다.")
    fields = []
    for item in value:
        field = str(item or "").strip()
        if not field:
            raise ValueError(f"front_matter.{name}에 빈 필드명이 있습니다.")
        if field not in fields:
            fields.append(field)
    return tuple(fields)


@dataclass(frozen=True)
class MarkdownFrontMatterResult:
    """front matter 1건의 분리 결과.

    filtered_text 가 None 이면 **본문에서 뺄 것이 없다**는 뜻이라 호출부가 원본 파일을 그대로
    docling 에 넘긴다(불필요한 임시 파일 생성 회피).
    """

    found: bool
    metadata: dict[str, Any]        # 승격 대상: {목표필드: 값}
    prompt_prefix: str              # 본문에서 뺐지만 LLM 프롬프트에는 남길 원문
    filtered_text: str | None       # front matter 를 걸러낸 md 전문(뺄 것이 없으면 None)
    source_fields: tuple[str, ...] = ()   # 원천 front matter 에 실제로 있던 키(로그/진단용)


@dataclass(frozen=True)
class MarkdownFrontMatterSpec:
    """`markdown.front_matter` 블록 하나를 컴파일한 선언.

    ```yaml
    markdown:
      front_matter:
        metadata_fields:            # 원천키: 목표필드 (list 로 쓰면 이름 그대로 승격)
          source_file: source_file
          created_at: created_date  # 청커 date_int transform 이 YYYYMMDD 정수로 바꾼다
        exclude_text_fields: ["*"]  # "*" = front matter 전체를 본문에서 제외
        on_missing: warn            # ignore(기본) | warn | error
        on_invalid: warn            # ignore(기본) | warn | error
        max_bytes: 65536            # front matter YAML 크기 상한
    ```
    """

    doc_types: tuple[str, ...]
    metadata_fields: dict[str, str]
    exclude_text_fields: tuple[str, ...]
    on_missing: str = "ignore"
    on_invalid: str = "ignore"
    max_bytes: int = _DEFAULT_MAX_BYTES

    @classmethod
    def from_config(cls, config: dict) -> MarkdownFrontMatterSpec:
        markdown_cfg = config.get("markdown")
        markdown_cfg = markdown_cfg if isinstance(markdown_cfg, dict) else {}
        fm_cfg = markdown_cfg.get("front_matter")
        if fm_cfg is True:
            fm_cfg = {}
        if not isinstance(fm_cfg, dict):
            raise ValueError("custom_fields.markdown.front_matter는 object여야 합니다.")

        metadata_fields = _metadata_field_map(fm_cfg.get("metadata_fields"))
        invalid_targets = sorted(set(metadata_fields.values()) & _RESERVED_TARGETS)
        if invalid_targets:
            raise ValueError(
                "front_matter metadata 대상이 청크 예약 필드와 충돌합니다: "
                f"{invalid_targets}"
            )

        exclude_fields = _field_list(fm_cfg.get("exclude_text_fields"), "exclude_text_fields")
        try:
            max_bytes = int(fm_cfg.get("max_bytes") or _DEFAULT_MAX_BYTES)
        except (TypeError, ValueError) as exc:
            raise ValueError("front_matter.max_bytes는 정수여야 합니다.") from exc
        if max_bytes < 1:
            raise ValueError("front_matter.max_bytes는 1 이상이어야 합니다.")

        return cls(
            doc_types=normalize_doc_types(config.get("doc_type")),
            metadata_fields=metadata_fields,
            exclude_text_fields=exclude_fields,
            on_missing=_policy(fm_cfg.get("on_missing"), "on_missing", "ignore"),
            on_invalid=_policy(fm_cfg.get("on_invalid"), "on_invalid", "ignore"),
            max_bytes=max_bytes,
        )

    def matches(self, runtime_doc_type: Any) -> bool:
        return not self.doc_types or normalize_doc_type(runtime_doc_type) in self.doc_types

    def _handle(self, policy: str, message: str, exc: Exception | None = None) -> MarkdownFrontMatterResult:
        if policy == "error":
            if exc is not None:
                raise ValueError(message) from exc
            raise ValueError(message)
        if policy == "warn":
            _log.warning(message)
        return MarkdownFrontMatterResult(False, {}, "", None)

    def parse(self, file_path: str | Path) -> MarkdownFrontMatterResult:
        path = Path(file_path)
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as exc:
            return self._handle(
                self.on_invalid,
                f"Markdown front matter 입력을 읽을 수 없습니다: {path.name} ({exc})",
                exc,
            )

        lines = text.splitlines(keepends=True)
        if not lines or lines[0].strip() != "---":
            return self._handle(
                self.on_missing,
                f"Markdown front matter가 없습니다: {path.name}",
            )

        closing = next(
            (idx for idx, line in enumerate(lines[1:], start=1) if line.strip() in {"---", "..."}),
            None,
        )
        if closing is None:
            return self._handle(
                self.on_invalid,
                f"Markdown front matter 종료 구분자가 없습니다: {path.name}",
            )

        yaml_text = "".join(lines[1:closing])
        if len(yaml_text.encode("utf-8")) > self.max_bytes:
            return self._handle(
                self.on_invalid,
                f"Markdown front matter가 max_bytes={self.max_bytes}를 초과했습니다: {path.name}",
            )

        try:
            parsed = yaml.load(yaml_text, Loader=_UniqueKeySafeLoader) or {}
        except Exception as exc:
            return self._handle(
                self.on_invalid,
                f"Markdown front matter YAML 파싱 실패: {path.name} ({exc})",
                exc,
            )
        if not isinstance(parsed, dict):
            return self._handle(
                self.on_invalid,
                f"Markdown front matter 최상위 값은 mapping이어야 합니다: {path.name}",
            )

        try:
            fields = _normalize_value(parsed)
        except ValueError as exc:
            return self._handle(
                self.on_invalid,
                f"Markdown front matter 값 정규화 실패: {path.name} ({exc})",
                exc,
            )
        selected = {
            target: fields[source]
            for source, target in self.metadata_fields.items()
            if source in fields
        }
        missing_selected = sorted(set(self.metadata_fields) - set(fields))
        if missing_selected:
            _log.warning(
                "Markdown front matter metadata_fields 누락(%s): %s",
                path.name,
                missing_selected,
            )

        # 승격(metadata_fields)과 제외(exclude_text_fields)는 서로 독립이다 — 승격하면서 본문에
        # 남길 수도, 승격 없이 빼기만 할 수도 있다. `"*"` 는 front matter 전체 제외를 뜻한다.
        exclude_all = "*" in self.exclude_text_fields
        excluded_names = (
            set(fields)
            if exclude_all
            else set(fields) & set(self.exclude_text_fields)
        )
        excluded = {key: value for key, value in fields.items() if key in excluded_names}
        remaining = {key: value for key, value in fields.items() if key not in excluded_names}

        filtered_text = None
        if excluded_names:
            body = "".join(lines[closing + 1:])
            # 일부만 남기는 경우 front matter 블록을 남은 키로 다시 써 준다(docling 이 이 줄들을
            # 본문 텍스트로 읽는 기존 동작 그대로). 전부 빼면 블록 자체를 없앤다.
            if remaining:
                kept_yaml = yaml.safe_dump(
                    remaining, allow_unicode=True, sort_keys=False, default_flow_style=False
                )
                filtered_text = f"---\n{kept_yaml}---\n{body}"
            else:
                filtered_text = body.lstrip("\r\n")

        # 본문에서 뺀 front matter 를 LLM 프롬프트에는 되돌려 준다 — product_slf 의 PRODUCT_C
        # 처럼 근거가 front matter 에만 있는 추출 필드가 있어, 여기서 빼면 추출이 불가능해진다.
        prompt_prefix = ""
        if excluded:
            excluded_yaml = yaml.safe_dump(
                excluded, allow_unicode=True, sort_keys=False, default_flow_style=False
            ).strip()
            prompt_prefix = f"[Markdown front matter]\n{excluded_yaml}"

        _log.info(
            "[markdown_front_matter] file=%s metadata=%s excluded_from_text=%s",
            path.name,
            sorted(selected),
            sorted(excluded),
        )
        return MarkdownFrontMatterResult(
            found=True,
            metadata=selected,
            prompt_prefix=prompt_prefix,
            filtered_text=filtered_text,
            source_fields=tuple(fields),
        )


def _merge_config(base: dict, override: dict) -> dict:
    """두 config mapping을 재귀 병합한다. override 값이 우선한다."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = _merge_config(merged[key], value)
        else:
            merged[key] = value
    return merged


def resolve_format_cfg(config: dict, block: str) -> dict | None:
    """custom_fields 항목 하나의 유효 ``<block>`` 설정을 해석한다.

    ``config_file`` 의 해당 블록이 기본값이고, 상위 custom_fields 항목에 같은 키가 있으면
    재귀적으로 덮어쓴다. 상위 ``<block>: false`` 는 하위 설정을 명시적으로 비활성화한다.
    ``markdown`` 과 ``html`` 이 같은 해석 규칙을 공유한다.

    대상이 아니면(문서 단위 extractor 아님/비활성) None.
    """
    inline_present = block in config
    inline_block = config.get(block)

    # 상위 설정에 잘못 배치한 블록은 하위 파일 로드 전에 명확히 잡는다.
    if (
        inline_present
        and inline_block not in (None, False)
        and custom_fields_extractor(config) not in DOCUMENT_CUSTOM_FIELD_EXTRACTORS
    ):
        raise ValueError(f"custom_fields.{block}은 문서 단위 extractor에서만 지원합니다.")

    if custom_fields_extractor(config) not in DOCUMENT_CUSTOM_FIELD_EXTRACTORS:
        return None

    child_cfg = load_custom_fields_config(
        str(config.get("config_file") or ""),
        str(config.get("resource_path") or "") or None,
    )
    child_block = child_cfg.get(block)
    child_block = child_block if isinstance(child_block, dict) else {}

    if inline_present and inline_block in (None, False):
        return None
    if inline_present and not isinstance(inline_block, dict):
        raise ValueError(f"custom_fields.{block}은 object 또는 false여야 합니다.")

    return _merge_config(child_block, inline_block or {})


def resolve_markdown_cfg(config: dict) -> dict | None:
    """custom_fields 항목 하나의 유효 ``markdown`` 블록을 해석한다.

    ``config_file``의 ``markdown`` 블록이 기본값이고, 상위 custom_fields 항목에
    ``markdown``이 있으면 재귀적으로 덮어쓴다. 상위 ``markdown: false``는 하위 설정을
    명시적으로 비활성화한다. 대상이 아니면(문서 단위 extractor 아님/비활성) None.

    ``markdown`` 하위 블록(front_matter · text_fence)의 빌더들이 공유한다.
    """
    return resolve_format_cfg(config, "markdown")


def build_markdown_front_matter_specs(configs: list[dict]) -> list[MarkdownFrontMatterSpec]:
    """문서 단위 custom_fields 설정들을 front matter spec 으로 컴파일한다(기동 시 1회).

    상위 ``markdown: false`` 또는 ``front_matter: false``는 명시적 비활성화다.
    """
    specs = []
    for config in configs or []:
        markdown_cfg = resolve_markdown_cfg(config)
        if markdown_cfg is None:
            continue
        if "front_matter" not in markdown_cfg or markdown_cfg.get("front_matter") in (None, False):
            continue

        effective_config = dict(config)
        effective_config["markdown"] = markdown_cfg
        specs.append(MarkdownFrontMatterSpec.from_config(effective_config))
    return specs


def build_markdown_text_fence_specs(configs: list[dict]) -> list[MarkdownTextFenceSpec]:
    """문서 단위 custom_fields 설정들을 text_fence spec 으로 컴파일한다(기동 시 1회).

    front matter 와 같은 ``markdown`` 블록을 공유하므로 해석부도 같이 쓴다
    (``resolve_markdown_cfg``). ``text_fence: false`` 또는 ``enable: false``는 비활성화다.
    """
    specs = []
    for config in configs or []:
        markdown_cfg = resolve_markdown_cfg(config)
        if markdown_cfg is None:
            continue
        fence_cfg = markdown_cfg.get("text_fence")
        if fence_cfg in (None, False):
            continue
        if isinstance(fence_cfg, dict) and fence_cfg.get("enable") is False:
            continue

        specs.append(
            MarkdownTextFenceSpec.from_config(
                fence_cfg, normalize_doc_types(config.get("doc_type"))
            )
        )
    return specs


def build_marker_heading_doc_types(configs: list[dict], fmt: str) -> frozenset[str]:
    """``<fmt>.marker_headings`` 를 켠 문서 단위 custom_fields 의 doc_type 집합.

    같은 원문이 html 로도 md 로도 올 수 있어 두 포맷이 같은 스위치를 갖는다. 판정 규칙은
    converters 쪽에서 공유하므로 여기서 갈리는 것은 어느 포맷 블록을 읽느냐뿐이다.
    """
    doc_types: set[str] = set()
    for config in configs or []:
        format_cfg = resolve_format_cfg(config, fmt)
        if format_cfg is None:
            continue
        marker_cfg = format_cfg.get("marker_headings")
        if marker_cfg in (None, False):
            continue
        if isinstance(marker_cfg, dict) and marker_cfg.get("enable") is False:
            continue
        doc_types.update(normalize_doc_types(config.get("doc_type")))
    return frozenset(doc_types)


def build_html_marker_heading_doc_types(configs: list[dict]) -> frozenset[str]:
    """``html.marker_headings`` 를 켠 문서 단위 custom_fields 의 doc_type 집합(기동 시 1회).

    스펙 객체를 만들지 않는 이유 — 이 전처리는 문서군마다 조정할 값이 없다(마커 집합·길이
    상한·종결형 판정은 한국 기업문서 공통이고 converters/html_flatten.py 의 근거 주석과 함께
    코드 상수로 있다). 켜고 끄는 doc_type 목록 하나로 충분하므로 설정 개념을 늘리지 않는다.
    상위 ``html: false`` 또는 ``marker_headings: false`` 는 명시적 비활성화다.
    """
    return build_marker_heading_doc_types(configs, "html")


def build_markdown_marker_heading_doc_types(configs: list[dict]) -> frozenset[str]:
    """``markdown.marker_headings`` 를 켠 문서 단위 custom_fields 의 doc_type 집합."""
    return build_marker_heading_doc_types(configs, "markdown")
