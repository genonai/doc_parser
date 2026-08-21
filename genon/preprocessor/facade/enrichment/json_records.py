"""JSON 레코드 배열의 custom_fields 매핑.

## 왜 필요한가

`eventList[*]` 처럼 **레코드 배열 안에 구조화된 필드와 본문 HTML 이 함께** 오는 입력이 있다.
적재 스키마가 "레코드 1건 = 1행"(제목/시작일/종료일/상세HTML/요약본문)이라 청크마다 서로 다른
메타데이터가 필요한데, 기존 `.json` 경로(`converters/json_text.py`)는 전체를 하나의 HTML 로 병합해
docling 으로 파싱하는 **문서 모드**여서 청크별 메타데이터를 실을 수 없다(docling 경로의 extra 는
문서 전역이다).

이 모듈은 형제 모듈 `tabular_custom_fields.py`(Excel/CSV 행 매핑)를 JSON 에 대응시킨 것이다.
같은 출력 계약(`category="custom_fields_row"` + `content` + `metadata`)을 쓰므로 청커의 행 기반
경로(`_chunk_custom_fields_rows`)가 그대로 소비한다 — 새 element category 를 만들지 않는다.

## 키 지정 방식

`converters/json_text.py` 와 동일하게 **키 이름만** 나열한다(JSONPath 등 경로 문법 없음).
레코드 안에서 임의 깊이를 BFS 로 훑어 얕은 쪽을 우선 채택하므로 `wcmsHtml.htmlText` 같은 중첩도
`htmlText` 한 단어로 잡힌다. 비교는 tabular 와 같은 정규화(Unicode/BOM/대소문자/공백·구분자)를 거친다.

## LLM 생성 필드

JSON 에 없는 필드(예: 상세내용 요약)는 `llm_fields` 로 선언만 하고, 실제 호출은 파서가
`CustomFieldsEnricher.extract_fields_from_text` 로 수행한다. 이 모듈은 순수 변환만 담당한다.
"""
from __future__ import annotations

import logging
from collections import deque
from pathlib import Path
from typing import Any

import yaml

_log = logging.getLogger(__name__)

from .custom_fields_enricher import (
    JSON_CUSTOM_FIELD_EXTRACTORS,
    VALID_LLM_ERROR_POLICIES,  # noqa: F401  (하위 호환 재노출)
    LlmFieldSpec,  # noqa: F401  (하위 호환 재노출 — 정의는 custom_fields_enricher 로 이동)
    build_llm_field_specs,
    custom_fields_extractor,
    matches_doc_type,
    normalize_doc_type,
    normalize_doc_types,
)
from .field_transforms import VALUE_TRANSFORMS
from .tabular_custom_fields import (
    apply_value_map,
    compile_value_map,
    normalize_column_name,
    validate_custom_field_config,
)

VALID_MISSING_POLICIES = ("error", "skip")

# 스칼라로 볼 값 타입. dict 는 "필드 값"이 아니므로 매칭 대상에서 제외하고 계속 파고든다.
_SCALAR_TYPES = (str, int, float, bool)


def _is_field_value(value: Any) -> bool:
    """이 값을 "필드 값"으로 채택할지 — 스칼라, 또는 스칼라만 담긴 배열.

    `related_keywords: []` 처럼 원천이 배열로 주는 JSON 컬럼(TB_CS_ITEM.RELATED_KEYWORDS,
    TB_FAQ.QUESTION_VARIANTS 등)을 받기 위해 스칼라 배열까지 허용한다.
    dict 와 dict 를 담은 배열은 여전히 값이 아니라 **구조**로 보고 계속 파고든다 —
    그래야 `eventList` 같은 레코드 배열이 실수로 필드 값으로 잡히지 않는다.
    """
    if isinstance(value, _SCALAR_TYPES):
        return True
    if isinstance(value, list):
        return all(isinstance(item, _SCALAR_TYPES) for item in value)
    return False


def _clean_value(value: Any) -> Any:
    """문자열 값의 BOM/양끝 공백 제거(tabular `_clean_cell` 과 같은 규칙). 배열은 원소별로 적용."""
    if isinstance(value, list):
        return [_clean_value(item) for item in value]
    if isinstance(value, str):
        return value.replace("\ufeff", "").strip()
    return value


def find_field(record: Any, aliases: list[str]) -> Any:
    """레코드 안에서 별칭 중 하나에 해당하는 스칼라 값을 찾는다(얕은 깊이 우선).

    별칭 순서보다 **깊이가 우선**한다 — 같은 레벨 안에서만 설정 순서를 따른다. 그래야
    최상위의 `제목` 이 중첩된 `title` 보다 먼저 잡혀 결과가 예측 가능해진다.
    각 레벨에서 정확 일치를 먼저 보고, 없으면 정규화 일치로 재시도한다.
    """
    exact_aliases = [alias for alias in aliases if alias]
    normalized_aliases = {normalize_column_name(alias) for alias in exact_aliases}

    level: list = [record]
    while level:
        dicts = [node for node in level if isinstance(node, dict)]
        for alias in exact_aliases:
            for node in dicts:
                if alias in node and _is_field_value(node[alias]):
                    return _clean_value(node[alias])
        for node in dicts:
            for key, value in node.items():
                if _is_field_value(value) and normalize_column_name(key) in normalized_aliases:
                    return _clean_value(value)

        next_level: list = []
        for node in level:
            if isinstance(node, dict):
                next_level.extend(node.values())
            elif isinstance(node, list):
                next_level.extend(node)
        level = next_level
    return None


def collect_records(payload: Any, records_key: str | None) -> list[dict] | None:
    """레코드(dict) 목록을 뽑는다. 키를 못 찾으면 None(호출측이 정책 결정).

    - `records_key` 미지정: payload 가 목록이면 그 안의 dict 들, dict 면 1건짜리 목록.
    - `records_key` 지정: 임의 깊이에서 그 이름의 키를 찾아 값이 list 면 그 안의 dict 들,
      dict 하나면 1건짜리 목록으로 본다.
    """
    if not records_key:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            return [payload]
        return None

    normalized_key = normalize_column_name(records_key)
    queue: deque = deque([payload])
    while queue:
        node = queue.popleft()
        if isinstance(node, dict):
            for key, value in node.items():
                if key != records_key and normalize_column_name(key) != normalized_key:
                    continue
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
                if isinstance(value, dict):
                    return [value]
            queue.extend(node.values())
        elif isinstance(node, list):
            queue.extend(node)
    return None


def html_to_text(value: Any) -> str:
    """HTML 문자열 → 평문. 정리 규칙은 파싱 경로(html_flatten)와 동일하게 맞춘다.

    `aria-hidden`/`display:none` 안의 본문(혜택 텍스트·접힌 약관)은 **보존**된다 —
    html_flatten 모듈 docstring 의 측정 근거 참고.
    """
    if not isinstance(value, str) or not value.strip():
        return ""
    from genon.preprocessor.converters.html_flatten import extract_content

    node = extract_content(value)
    # 태그 경계마다 빈 줄이 생기므로 공백 줄을 접어 프롬프트 토큰 낭비를 막는다.
    lines = (line.strip() for line in node.get_text("\n").splitlines())
    return "\n".join(line for line in lines if line)


class JsonRecordsMapper:
    """custom_fields 설정 하나를 JSON 레코드 → parse-format 변환기로 컴파일한다.

    tabular 의 `TabularCustomFieldsMapper` 와 같은 역할·수명(기동 시 1회 생성).
    """

    def __init__(
        self,
        *,
        config_file: str = "",
        resource_path: str | None = None,
        doc_type: str | list[str] | None = None,
        extractor: str = "json_mapping",
        **_: Any,
    ) -> None:
        if str(extractor or "").strip().lower() not in JSON_CUSTOM_FIELD_EXTRACTORS:
            raise ValueError(f"지원하지 않는 json custom_fields extractor: {extractor}")
        self.doc_types = normalize_doc_types(doc_type)
        # 프롬프트/LLM config 파일 경로 해석 기준(= 이 config 파일과 같은 디렉토리).
        self.resource_path = resource_path
        cfg = self._load_config(config_file, resource_path)

        self.records_key: str | None = str(cfg.get("records") or "").strip() or None

        key_map = cfg.get("key_map") or {}
        if not isinstance(key_map, dict) or not key_map:
            raise ValueError("json_mapping custom_fields 에는 key_map 이 필요합니다.")
        # 목표필드명 자체를 자동 별칭으로 포함(tabular column_map 과 동일 규칙).
        self.key_map: dict[str, list[str]] = {
            str(target): self._aliases(str(target), sources)
            for target, sources in key_map.items()
        }

        self.required = list(cfg.get("required") or [])
        self.nulls = list(cfg.get("nulls") or [])
        self.defaults = dict(cfg.get("defaults") or {})
        self.constants = dict(cfg.get("constants") or {})

        # 값 별칭 정규화(GROUP_C 의 "삼성생명/생명/SLF" 흔들림 등). tabular 와 같은 구현을 공유한다.
        self.value_map = compile_value_map(cfg.get("value_map"))

        self.transforms = {str(k): str(v) for k, v in (cfg.get("transforms") or {}).items()}
        unknown = sorted({name for name in self.transforms.values() if name not in VALUE_TRANSFORMS})
        if unknown:
            raise ValueError(
                f"등록되지 않은 transforms 변환기: {unknown} (사용 가능: {sorted(VALUE_TRANSFORMS)})"
            )

        self.html_text_fields = {str(k): str(v) for k, v in (cfg.get("html_text_fields") or {}).items()}
        self.llm_field_specs = build_llm_field_specs(cfg)

        self.text_fields = [str(f).strip() for f in (cfg.get("text_fields") or []) if str(f).strip()]
        if not self.text_fields:
            raise ValueError("json_mapping custom_fields 에는 text_fields(청크 본문 구성)가 필요합니다.")

        self.split = bool(cfg.get("split", False))

        policy = str(cfg.get("missing_policy") or "error").strip().lower()
        if policy not in VALID_MISSING_POLICIES:
            _log.warning(f"[json_records] Invalid missing_policy '{policy}', fallback to 'error'")
            policy = "error"
        self.missing_policy = policy

        # 설정 오기입을 여기서 막는다(tabular 와 동일 기준).
        validate_custom_field_config(cfg, label=f"json custom_fields({config_file})")

    # ── 설정 로딩 ────────────────────────────────────────────────────────────
    @staticmethod
    def _load_config(config_file: str, resource_path: str | None) -> dict:
        if not config_file:
            raise ValueError("json_mapping custom_fields 에는 config_file 이 필요합니다.")
        path = Path(config_file)
        if not path.is_absolute() and resource_path:
            path = Path(resource_path) / path
        path = path.resolve()
        if not path.is_file():
            raise FileNotFoundError(f"json custom_fields config 없음: {path}")
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"json custom_fields config 는 object 여야 합니다: {path}")
        return loaded

    @staticmethod
    def _aliases(target: str, source_spec: Any) -> list[str]:
        values = source_spec if isinstance(source_spec, list) else [source_spec]
        aliases = [target]
        for value in values:
            value = str(value or "").strip()
            if value and value not in aliases:
                aliases.append(value)
        return aliases

    # ── 매칭 ─────────────────────────────────────────────────────────────────
    def matches(self, runtime_doc_type: Any) -> bool:
        return matches_doc_type(self.doc_types, runtime_doc_type)

    def canonical_doc_type(self, runtime_doc_type: Any) -> str:
        runtime = normalize_doc_type(runtime_doc_type)
        if runtime and runtime in self.doc_types:
            return runtime
        return self.doc_types[0] if self.doc_types else runtime

    # ── 변환 ─────────────────────────────────────────────────────────────────
    def extract_records(self, payload: Any) -> list[dict]:
        """payload 에서 레코드 목록을 뽑는다. 못 찾으면 missing_policy 에 따라 처리."""
        records = collect_records(payload, self.records_key)
        if records is None:
            msg = f"records 키 '{self.records_key}' 를 JSON 에서 찾지 못했습니다."
            if self.missing_policy == "error":
                raise ValueError(msg)
            _log.warning(f"[json_records] {msg} — 레코드 0건으로 진행합니다.")
            return []
        return records

    def map_record(self, record: dict) -> dict:
        """레코드 1건 → 목표필드 dict(변환/기본값/파생 필드까지 적용)."""
        fields: dict[str, Any] = {name: None for name in self.nulls}
        for target, aliases in self.key_map.items():
            fields[target] = find_field(record, aliases)

        fields.update(self.constants)
        for key, value in self.defaults.items():
            if fields.get(key) in (None, ""):
                fields[key] = value

        # 값 정규화 → 변환 순서. 별칭을 표준값으로 접은 뒤에 타입 변환을 건다(tabular 와 동일).
        apply_value_map(fields, self.value_map)

        for target, transform_name in self.transforms.items():
            fields[target] = VALUE_TRANSFORMS[transform_name](fields.get(target))

        for target, source in self.html_text_fields.items():
            fields[target] = html_to_text(fields.get(source))

        return fields

    def missing_required(self, fields: dict) -> list[str]:
        return [name for name in self.required if fields.get(name) in (None, "")]

    def build_fields(self, payload: Any, runtime_doc_type: Any) -> list[dict]:
        """payload → 레코드별 목표필드 목록. 필수값 누락 레코드는 skip(요약 경고)."""
        records = self.extract_records(payload)
        doc_type = self.canonical_doc_type(runtime_doc_type)

        mapped: list[dict] = []
        skipped = 0
        for index, record in enumerate(records):
            fields = self.map_record(record)
            missing = self.missing_required(fields)
            if missing:
                skipped += 1
                _log.warning(
                    f"[json_records] 필수값 누락 레코드 skip(index={index}): {sorted(missing)}"
                )
                continue
            if doc_type:
                # 요청/프로파일에서 확정한 값이 config constants 보다 우선한다.
                fields["doc_type"] = doc_type
            mapped.append(fields)

        if skipped:
            # silent 축소 방지 — 몇 건이 빠졌는지 요약으로 드러낸다.
            _log.warning(
                f"[json_records] skipped {skipped}/{len(records)} records (missing required)"
            )
        return mapped

    def build_text(self, fields: dict) -> str:
        """청크 본문 — text_fields 를 선언 순서대로 개행 결합(tabular 와 같은 규칙)."""
        return "\n".join(
            str(fields[name]) for name in self.text_fields if fields.get(name) not in (None, "")
        )

    def to_parse_format(self, fields_list: list[dict], runtime_doc_type: Any) -> dict:
        """목표필드 목록 → parse-format(청커 행 기반 경로가 소비하는 형태).

        본문이 빈 레코드는 element 로 내보내지 않는다. 청커의 행 경로는 빈 content 도 청크로
        만들기 때문에(tabular 와 동일), 그대로 두면 text 가 빈 벡터가 적재된다. text_fields 가
        LLM 생성 필드뿐인 설정에서 호출이 실패하면 바로 이 상황이 되므로 건수를 경고로 남긴다.
        """
        doc_type = self.canonical_doc_type(runtime_doc_type)
        elements = []
        empty = 0
        for fields in fields_list:
            content = self.build_text(fields)
            if not content.strip():
                empty += 1
                continue
            element = {
                "category": "custom_fields_row",
                "content": content,
                "coordinates": [],
                "id": len(elements),
                "page": len(elements) + 1,
                "metadata": fields,
            }
            if self.split:
                # 청커가 chunk_size 초과 시 이 element 만 여러 청크로 나눈다
                # (tabular/faq 의 "행 1개 = 청크 1개" 동작은 그대로 유지).
                element["splittable"] = True
            elements.append(element)

        if empty:
            # silent 축소 방지 — 몇 건이 본문 없이 빠졌는지 드러낸다.
            _log.warning(
                f"[json_records] 본문이 빈 레코드 {empty}/{len(fields_list)}건을 제외했습니다 "
                f"(text_fields={self.text_fields})"
            )

        result: dict[str, Any] = {"elements": elements, "usage": {"pages": len(elements)}}
        if doc_type:
            result["metadata"] = {"doc_type": doc_type}
        return result


def build_json_records_mappers(configs: list[dict]) -> list[JsonRecordsMapper]:
    """custom_fields 설정 중 json_mapping 만 매퍼로 컴파일한다(tabular 와 같은 패턴)."""
    return [
        JsonRecordsMapper(**dict(config))
        for config in (configs or [])
        if custom_fields_extractor(config) in JSON_CUSTOM_FIELD_EXTRACTORS
    ]
