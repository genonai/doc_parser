"""custom_fields 설정 v2 스키마 — **기존 내부 형태로 정규화하는 앞단**.

## 설계 원칙: 파이프라인을 새로 쓰지 않는다

v2 는 매퍼가 읽는 내부 dict 모양을 그대로 만들어 주는 번역기다. 매퍼(tabular/json_records/
json_semantic/custom_fields_enricher)는 한 줄도 바뀌지 않는다.

    v2 yaml ──normalize()──► 내부(v1) 형태 ──► 기존 매퍼
    v1 yaml ────────────────► 내부(v1) 형태 ──► 기존 매퍼   (종전 그대로)

이렇게 두면 **동작 동일성이 테스트가 아니라 구조로 보장된다.** 두 스키마가 같은 코드를
타므로 "v2 로 옮겼더니 청크가 달라졌다"가 원리상 생길 수 없고, 남는 위험은 번역이 틀리는
것뿐이다. 그건 `to_v2()` → `normalize()` 왕복이 원본과 같은지 대조해 잡는다
(examples/config_precheck/verify_v2_equivalence.py).

## 왜 v2 인가

v1 은 최상위 키가 25개를 넘고, **한 필드의 규칙이 6개 블록에 흩어진다.**

    column_map:   {SEARCHABLE_YN: [노출여부]}      # 어디서 오는가
    value_map:    {SEARCHABLE_YN: {...}}           # 값을 어떻게 접는가
    defaults:     {SEARCHABLE_YN: "N"}             # 비면 무엇을 넣는가

셋을 다 보려면 파일을 세 번 훑어야 하고, 하나를 지울 때 나머지를 잊는다. v2 는 필드 하나의
규칙을 한 자리에 모은다.

    fields:
      SEARCHABLE_YN: {alias: [노출여부], values: {...}, default: "N"}

## 최상위 키는 7개

`schema` `source` `fields` `require` `filter` `body` `llm`

`doc_type` 은 **여기 두지 않는다** — faq(tabular+json_mapping), product_hpp(llm+json_semantic)
처럼 doc_type 하나에 파일이 둘인 경우가 있어 등록(registry)은 계속 프로세서 config 가 한다.
"""

from __future__ import annotations

from typing import Any

SCHEMA_KEY = "schema"
SCHEMA_V2 = "v2"

# source.kind → 이 kind 를 처리하는 extractor(등록 블록의 extractor 와 대조해 오배치를 잡는다).
KIND_TO_EXTRACTOR = {
    "rows": "tabular_mapping",
    "records": "json_mapping",
    "sections": "json_semantic",
    "document": "llm",
}

TOP_LEVEL_KEYS = frozenset({
    SCHEMA_KEY, "source", "fields", "require", "filter", "body", "llm",
})

# 필드 스펙 안에 쓸 수 있는 키. 값은 **항상 dict** 다 — 리스트/스칼라 단축형을 받지 않는다.
# `TARGET_A:` 처럼 값을 빠뜨린 오타가 null 로 파싱돼 조용히 통과하는 것을 막기 위해서다.
FIELD_SPEC_KEYS = frozenset({
    "alias", "const", "default", "values", "transform", "from", "as", "collect",
})
SOURCE_KEYS = frozenset({
    "kind", "records_at", "table_at", "on_missing", "merge_rows",
    "sections", "ignore_keys", "pre",
})
BODY_KEYS = frozenset({"fields", "labels", "split", "repeat", "once", "mirror_to"})
# source.pre 아래 쓸 수 있는 원천 포맷 전처리 블록(파서가 소비한다).
PRE_KEYS = ("markdown", "html")
REQUIRE_KEYS = frozenset({"fields"})

# ── v2 ↔ 내부(v1) 매핑은 **여기 한 벌만** 둔다 ──────────────────────────────
# normalize() 와 to_v2() 가 같은 표를 양방향으로 읽는다. 방향마다 표를 따로 두면 한쪽만
# 고쳐져 "v2 로 옮겼더니 값이 사라지는" 번역 결함이 생기고, 그건 왕복 검증에도 안 잡힌다
# (양쪽이 똑같이 흘리면 왕복은 통과한다).

# `as:` 가 고르는 파생 블록. auto 는 값의 종류를 자동 판별한다(text_from).
_AS_TO_BLOCK = {"auto": "text_from", "html": "html_text_fields"}

# kind 별로 별칭 매핑이 들어가는 v1 키.
_ALIAS_BLOCK = {
    "rows": "column_map",
    "records": "key_map",
    "sections": "shared_fields",
}

# 필드 스펙 키 → 그 값이 들어가는 v1 블록(`{목표필드: 값}` 모양이 같은 것들).
# alias/from/as 는 kind·as 값에 따라 블록이 달라져 위 표가 따로 맡는다.
_SPEC_TO_BLOCK = {
    "collect": "collect_key_map",
    "const": "constants",
    "default": "defaults",
    "values": "value_map",
    "transform": "transforms",
}
_BLOCK_TO_SPEC = {v: k for k, v in _SPEC_TO_BLOCK.items()}

# body 블록 키 → v1 키.
_BODY_TO_V1 = {
    "fields": "text_fields",
    "labels": "field_labels",
    "split": "split",
    "repeat": "chunk_prefix_fields",
    "once": "first_chunk_fields",
    "mirror_to": "body_fields",
}

# source 블록 키 → (v1 키, 이 키를 쓸 수 있는 kind).
_SOURCE_TO_V1 = {
    "records_at": ("records", ("records",)),
    "on_missing": ("missing_policy", ("records", "sections")),
    "merge_rows": ("row_merge", ("rows", "records")),
    "sections": ("sections", ("sections",)),
    "ignore_keys": ("ignore_keys", ("sections",)),
}

# 필드 스펙이 만들 수 있는 모든 v1 블록(왕복 커버리지 계산에 쓴다).
_FIELD_BLOCKS = (
    set(_SPEC_TO_BLOCK.values()) | set(_ALIAS_BLOCK.values()) | set(_AS_TO_BLOCK.values())
)


class ConfigV2Error(ValueError):
    """v2 설정 오류. 메시지에 파일명과 문제 지점을 담는다."""


def is_v2(cfg: dict) -> bool:
    """이 설정이 v2 인가. `schema: v2` 한 줄로만 판단한다(추측하지 않는다)."""
    return str((cfg or {}).get(SCHEMA_KEY) or "").strip().lower() == SCHEMA_V2


def _require_dict(value: Any, label: str, what: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigV2Error(f"{label}: {what} 는 '키: 값' 형태의 object 여야 합니다.")
    return value


def _require_list(value: Any, label: str, what: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigV2Error(f"{label}: {what} 는 목록이어야 합니다(각 항목 앞에 '- ').")
    return value


def _check_unknown(keys, allowed, label: str, what: str) -> None:
    unknown = sorted(str(k) for k in keys if str(k) not in allowed)
    if unknown:
        raise ConfigV2Error(
            f"{label}: {what} 에 쓸 수 없는 키가 있습니다: {unknown}. "
            f"쓸 수 있는 키: {sorted(allowed)}"
        )


def normalize(cfg: dict, *, label: str = "custom_fields") -> tuple[dict, str]:
    """v2 설정 → `(내부(v1) 형태 dict, extractor 이름)`.

    이 함수의 출력은 기존 매퍼가 읽는 것과 **완전히 같은 모양**이어야 한다. 새 의미를
    만들지 않는다 — v2 는 표기만 다르다.
    """
    _check_unknown(cfg, TOP_LEVEL_KEYS, label, "최상위")

    source = _require_dict(cfg.get("source"), label, "source")
    _check_unknown(source, SOURCE_KEYS, label, "source")
    kind = str(source.get("kind") or "").strip().lower()
    if kind not in KIND_TO_EXTRACTOR:
        raise ConfigV2Error(
            f"{label}: source.kind 는 {sorted(KIND_TO_EXTRACTOR)} 중 하나여야 합니다: {kind!r}"
        )
    extractor = KIND_TO_EXTRACTOR[kind]
    out: dict[str, Any] = {}

    _normalize_source(source, kind, out, label)
    _normalize_fields(cfg.get("fields"), kind, out, label)
    _normalize_require(cfg.get("require"), kind, out, label)
    _normalize_body(cfg.get("body"), kind, out, label)
    _normalize_llm(cfg.get("llm"), kind, out, label)

    if cfg.get("filter"):
        # C2 에서 구현한다. 지금 조용히 무시하면 "필터가 걸린 줄 알았는데 전건이 나가는"
        # 무증상 실패가 되므로 여기서 막는다.
        raise ConfigV2Error(
            f"{label}: filter 는 아직 구현되지 않았습니다(값 기반 레코드 필터). "
            f"지금은 required 로 빈 값만 거를 수 있습니다."
        )
    return out, extractor


def _normalize_source(source: dict, kind: str, out: dict, label: str) -> None:
    if source.get("table_at") is not None:
        raise ConfigV2Error(
            f"{label}: source.table_at 는 아직 구현되지 않았습니다(표 N개 중 선택)."
        )
    for v2_key, (v1_key, kinds) in _SOURCE_TO_V1.items():
        if source.get(v2_key) is None:
            continue
        if kind not in kinds:
            raise ConfigV2Error(
                f"{label}: source.{v2_key} 는 kind: {'/'.join(kinds)} 전용입니다."
            )
        out[v1_key] = source[v2_key]
    pre = _require_dict(source.get("pre"), label, "source.pre")
    if pre:
        # 원천 포맷 전처리는 enricher 가 아니라 parser 가 소비한다. 내부 형태에서는 최상위
        # `markdown:`/`html:` 이므로 그대로 되돌린다(WIRING_KEYS 라 검증기도 허용한다).
        _check_unknown(pre, PRE_KEYS, label, "source.pre")
        for key in PRE_KEYS:
            if pre.get(key) is not None:
                out[key] = pre[key]


def _normalize_fields(fields: Any, kind: str, out: dict, label: str) -> None:
    fields = _require_dict(fields, label, "fields")
    alias_block = _ALIAS_BLOCK.get(kind)
    for name, spec in fields.items():
        target = str(name)
        where = f"{label}: fields.{target}"
        if not isinstance(spec, dict):
            raise ConfigV2Error(
                f"{where} 는 object 여야 합니다(예: `{target}: {{alias: [원천명]}}`). "
                f"값을 빠뜨리면 조용히 무시되므로 단축 표기를 받지 않습니다."
            )
        _check_unknown(spec, FIELD_SPEC_KEYS, where, "필드 스펙")

        if "alias" in spec:
            if alias_block is None:
                raise ConfigV2Error(f"{where}: kind: document 에는 alias 를 쓸 수 없습니다.")
            out.setdefault(alias_block, {})[target] = _require_list(
                spec["alias"], where, "alias"
            )
        if "collect" in spec:
            if kind != "records":
                raise ConfigV2Error(f"{where}: collect 는 kind: records 전용입니다.")
            out.setdefault("collect_key_map", {})[target] = _require_list(
                spec["collect"], where, "collect"
            )
        for spec_key, block in _SPEC_TO_BLOCK.items():
            if spec_key in ("collect",) or spec_key not in spec:
                continue  # collect 는 kind 제약이 있어 위에서 따로 다룬다
            value = spec[spec_key]
            if spec_key == "values":
                value = _require_dict(value, where, "values")
            out.setdefault(block, {})[target] = value
        if "from" in spec:
            as_kind = str(spec.get("as") or "auto").strip().lower()
            block = _AS_TO_BLOCK.get(as_kind)
            if block is None:
                raise ConfigV2Error(
                    f"{where}: as 는 {sorted(_AS_TO_BLOCK)} 중 하나여야 합니다: {as_kind!r}"
                )
            out.setdefault(block, {})[target] = spec["from"]
        elif "as" in spec:
            raise ConfigV2Error(f"{where}: as 는 from 과 함께 써야 합니다.")


def _normalize_require(require: Any, kind: str, out: dict, label: str) -> None:
    require = _require_dict(require, label, "require")
    if not require:
        return
    _check_unknown(require, REQUIRE_KEYS, label, "require")
    fields = _require_list(require.get("fields"), label, "require.fields")
    if not fields:
        return
    out["required_shared_fields" if kind == "sections" else "required"] = fields


def _normalize_body(body: Any, kind: str, out: dict, label: str) -> None:
    body = _require_dict(body, label, "body")
    if not body:
        return
    _check_unknown(body, BODY_KEYS, label, "body")
    for v2_key, v1_key in _BODY_TO_V1.items():
        if body.get(v2_key) is not None:
            out[v1_key] = body[v2_key]


def _normalize_llm(llm: Any, kind: str, out: dict, label: str) -> None:
    """`llm:` 목록 → 문서형은 최상위 키로, 레코드형은 `llm_fields` 로."""
    items = _require_list(llm, label, "llm")
    if not items:
        return
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ConfigV2Error(f"{label}: llm[{index}] 는 object 여야 합니다.")
        scope = str(item.get("scope") or ("document" if kind == "document" else "record"))
        flat = _flatten_llm_item(item, f"{label}: llm[{index}]")
        if scope == "document":
            if kind != "document":
                raise ConfigV2Error(
                    f"{label}: llm[{index}].scope=document 는 kind: document 전용입니다."
                )
            out.update(flat)
        else:
            out.setdefault("llm_fields", []).append(flat)


def _flatten_llm_item(item: dict, where: str) -> dict:
    """v2 의 endpoint/params/prompt 묶음을 v1 의 평평한 키로 편다."""
    flat: dict[str, Any] = {}
    for key, value in item.items():
        if key == "scope":
            continue
        if key == "out":
            flat["output_fields"] = value
        elif key == "in":
            flat["input_fields"] = value
        elif key in ("endpoint", "params"):
            flat.update(_require_dict(value, where, key))
        elif key == "prompt":
            prompt = _require_dict(value, where, "prompt")
            for pkey, pvalue in prompt.items():
                mapped = {
                    "system": "system_prompt", "user": "user_prompt",
                    "system_file": "system_prompt_file", "user_file": "user_prompt_file",
                    "variables": "variables",
                }.get(pkey)
                if mapped is None:
                    if pkey == "mode":
                        flat["template"] = {"mode": pvalue}
                        continue
                    raise ConfigV2Error(f"{where}: prompt.{pkey} 는 쓸 수 없는 키입니다.")
                flat[mapped] = pvalue
        else:
            flat[key] = value
    return flat


# ── v1 → v2 (마이그레이션 + 병행 검증) ──────────────────────────────────────
# `normalize()` 의 역방향. 이 둘의 왕복이 원본과 같아야 v2 가 v1 을 온전히 표현한다는 뜻이다.

_EXTRACTOR_TO_KIND = {v: k for k, v in KIND_TO_EXTRACTOR.items()}
# v1 블록 → v2 필드 스펙 키. 위 단일 표에서 파생한다(직접 적지 않는다).
_BLOCK_TO_SPEC_KEY = {
    **{block: "alias" for block in _ALIAS_BLOCK.values()},
    **_BLOCK_TO_SPEC,
}
# v1 의 llm 문서형 최상위 키 → v2 llm 항목 안에서의 자리.
_LLM_ENDPOINT_KEYS = ("url", "api_key", "model")
_LLM_PARAM_KEYS = ("max_tokens", "temperature", "timeout", "thinking", "thinking_dialect")
_LLM_PROMPT_KEYS = {
    "system_prompt": "system", "user_prompt": "user",
    "system_prompt_file": "system_file", "user_prompt_file": "user_file",
    "variables": "variables",
}


def to_v2(cfg: dict, extractor: str) -> dict:
    """v1 설정 → v2 설정. 값은 그대로 옮기고 자리만 바꾼다."""
    kind = _EXTRACTOR_TO_KIND.get(str(extractor or "llm").strip().lower())
    if kind is None:
        raise ConfigV2Error(f"v2 로 옮길 수 없는 extractor: {extractor}")

    out: dict[str, Any] = {SCHEMA_KEY: SCHEMA_V2, "source": {"kind": kind}}
    fields: dict[str, dict] = {}

    for v1_key, spec_key in _BLOCK_TO_SPEC_KEY.items():
        for target, value in (cfg.get(v1_key) or {}).items():
            fields.setdefault(str(target), {})[spec_key] = value
    for v1_key, as_kind in (("text_from", "auto"), ("html_text_fields", "html")):
        for target, source in (cfg.get(v1_key) or {}).items():
            spec = fields.setdefault(str(target), {})
            spec["from"] = source
            spec["as"] = as_kind
    if fields:
        out["fields"] = fields

    source = out["source"]
    for v2_key, (v1_key, _kinds) in _SOURCE_TO_V1.items():
        if cfg.get(v1_key) is not None:
            source[v2_key] = cfg[v1_key]
    pre = {k: cfg[k] for k in PRE_KEYS if cfg.get(k) is not None}
    if pre:
        source["pre"] = pre

    required = cfg.get("required_shared_fields") if kind == "sections" else cfg.get("required")
    if required:
        out["require"] = {"fields": required}

    body = {}
    for v2_key, v1_key in _BODY_TO_V1.items():
        if cfg.get(v1_key) is not None:
            body[v2_key] = cfg[v1_key]
    if body:
        out["body"] = body

    llm = []
    if kind == "document":
        document_item = _document_llm_item(cfg)
        if document_item:
            llm.append(document_item)
    for spec in (cfg.get("llm_fields") or []):
        llm.append(_record_llm_item(spec))
    if llm:
        out["llm"] = llm

    # 남은 키는 v2 가 아직 표현하지 못하는 것이다 — 조용히 버리면 왕복 검증이 통과해 버린다.
    covered = COVERED_V1_KEYS
    leftover = sorted(k for k in cfg if str(k) not in covered)
    if leftover:
        raise ConfigV2Error(f"v2 로 옮기지 못한 키가 있습니다: {leftover}")
    return out


def _document_llm_item(cfg: dict) -> dict:
    """문서형 v1 최상위 키를 v2 llm 항목 하나로 묶는다."""
    item: dict[str, Any] = {}
    endpoint = {k: cfg[k] for k in _LLM_ENDPOINT_KEYS if k in cfg}
    params = {k: cfg[k] for k in _LLM_PARAM_KEYS if k in cfg}
    prompt = {v2: cfg[v1] for v1, v2 in _LLM_PROMPT_KEYS.items() if v1 in cfg}
    if isinstance(cfg.get("template"), dict) and "mode" in cfg["template"]:
        prompt["mode"] = cfg["template"]["mode"]
    if cfg.get("output_fields") is not None:
        item["out"] = cfg["output_fields"]
    for key in ("parser", "pages", "table_text_description"):
        if cfg.get(key) is not None:
            item[key] = cfg[key]
    if endpoint:
        item["endpoint"] = endpoint
    if params:
        item["params"] = params
    if prompt:
        item["prompt"] = prompt
    if item:
        item = {"scope": "document", **item}
    return item


def _record_llm_item(spec: dict) -> dict:
    """`llm_fields` 항목 하나 → v2 llm 항목(scope: record)."""
    item: dict[str, Any] = {"scope": "record"}
    endpoint = {k: spec[k] for k in _LLM_ENDPOINT_KEYS if k in spec}
    params = {k: spec[k] for k in _LLM_PARAM_KEYS if k in spec}
    prompt = {v2: spec[v1] for v1, v2 in _LLM_PROMPT_KEYS.items() if v1 in spec}
    if spec.get("output_fields") is not None:
        item["out"] = spec["output_fields"]
    if spec.get("input_fields") is not None:
        item["in"] = spec["input_fields"]
    for key, value in spec.items():
        if key in _LLM_ENDPOINT_KEYS or key in _LLM_PARAM_KEYS or key in _LLM_PROMPT_KEYS:
            continue
        if key in ("output_fields", "input_fields", "template"):
            continue
        item[key] = value
    if isinstance(spec.get("template"), dict) and "mode" in spec["template"]:
        prompt["mode"] = spec["template"]["mode"]
    if endpoint:
        item["endpoint"] = endpoint
    if params:
        item["params"] = params
    if prompt:
        item["prompt"] = prompt
    return item


def load(loaded: dict, *, label: str) -> tuple[dict, str | None]:
    """설정 파일 내용 → `(내부(v1) 형태, v2 면 extractor 이름)`.

    v1 이면 그대로 돌려준다(`extractor` 는 None). 매퍼의 `_load_config` 끝에서 이 함수를
    한 번 거치게 하면, 그 아래 코드는 v1/v2 를 구분할 필요가 없다.
    """
    if not is_v2(loaded):
        return loaded, None
    return normalize(loaded, label=label)


# v2 가 표현할 수 있는 v1 키 전체. 위 단일 표에서 파생하므로 표를 고치면 여기도 따라온다.
# `tests/unit/test_config_v2_unit.py` 가 이 집합이 config_schema.EXTRACTOR_KEYS 를 덮는지
# 지켜, v1 에 새 키를 넣고 v2 를 잊는 드리프트를 배포 전에 잡는다.
COVERED_V1_KEYS = (
    _FIELD_BLOCKS
    | {v1 for v1, _kinds in _SOURCE_TO_V1.values()}
    | set(_BODY_TO_V1.values())
    | {"required", "required_shared_fields", "llm_fields"}
    | set(_LLM_ENDPOINT_KEYS) | set(_LLM_PARAM_KEYS) | set(_LLM_PROMPT_KEYS)
    | {"output_fields", "parser", "pages", "template", "table_text_description", "prompt"}
    | set(PRE_KEYS)
)
