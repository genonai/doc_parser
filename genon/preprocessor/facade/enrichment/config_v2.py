"""custom_fields 설정 v2 스키마 — **기존 내부 형태로 정규화하는 앞단**.

## 설계 원칙: 파이프라인을 새로 쓰지 않는다

v2 는 매퍼가 읽는 내부 dict 모양을 그대로 만들어 주는 번역기다. 매퍼(tabular/json_records/
json_semantic/custom_fields_enricher)는 이 내부 형태만 읽는다.

    yaml ──normalize()──► 내부 형태 ──► 매퍼

표기와 내부 형태를 갈라 두면 **설정을 정리해도 파이프라인이 흔들리지 않는다.** 매퍼는
번역 결과만 보므로, 남는 위험은 번역이 틀리는 것뿐이고 그건 아래 매핑 표 한 벌과
`COVERED_V1_KEYS` 드리프트 가드가 지킨다.

## 왜 이 표기인가

폐기된 옛 표기는 최상위 키가 25개를 넘고, **한 필드의 규칙이 6개 블록에 흩어졌다.**

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

import difflib
from typing import Any

SCHEMA_KEY = "schema"
SCHEMA_V2 = "v2"

# source.kind → 이 kind 를 처리하는 extractor(등록 블록의 extractor 와 대조해 오배치를 잡는다).
KIND_TO_EXTRACTOR = {
    "rows": "tabular_mapping",
    "records": "json_mapping",
    "sections": "json_semantic",
    "document": "llm",
    # 문서 단위인 것은 document 와 같다(docling 이 본문을 청킹한다). 다른 것은 값을 만드는
    # 주체다 — LLM 대신 선택자가 원문 HTML 에서 직접 뽑는다.
    "html": "html_select",
}

TOP_LEVEL_KEYS = frozenset({
    SCHEMA_KEY, "source", "fields", "require", "filter", "body", "llm", "python",
    # 프로세서 공통 표 설명의 문서유형별 오버라이드. 값 매핑이 아니라 기능 스위치라
    # v1/v2 표기가 같다 — 개념을 하나 더 만들지 않는다.
    "table_text_description",
})

# 필드 스펙 안에 쓸 수 있는 키. 값은 **항상 dict** 다 — 리스트/스칼라 단축형을 받지 않는다.
# `TARGET_A:` 처럼 값을 빠뜨린 오타가 null 로 파싱돼 조용히 통과하는 것을 막기 위해서다.
FIELD_SPEC_KEYS = frozenset({
    "alias", "const", "default", "values", "transform", "collect", "template", "seq",
    "pack", "raw",
    # kind: html 전용. `alias`(원천 key 이름)와 같은 역할의 같은 자리다 — 이 필드가
    # 원천의 어디서 오는가. `attr` 은 그 요소의 속성값을 쓸 때만 곁들인다.
    "select", "attr",
})
SOURCE_KEYS = frozenset({
    "kind", "records_at", "table_at", "on_missing", "merge_rows",
    "sections", "ignore_keys", "pre",
})
BODY_KEYS = frozenset({"fields", "labels", "split", "repeat", "once", "mirror_to"})
# source.pre 아래 쓸 수 있는 원천 포맷 전처리 블록(파서가 소비한다).
PRE_KEYS = ("markdown", "html", "delimited", "json")
# `pre` 바로 아래 쓸 수 있는 공통 스위치. **md 와 html 이 판정 규칙을 공유하는 것만** 여기
# 둔다 — 같은 값을 두 블록에 두 번 적게 하지 않기 위해서다(개념 수를 늘리지 않는다).
# 아래 블록에 같은 키를 명시하면 그쪽이 이긴다.
PRE_SHARED_KEYS = ("marker_headings",)
# 공통 스위치를 펼칠 대상. delimited·json 은 원천의 모양을 바꾸는 기구라 받지 않는다.
_PRE_SHARED_TARGETS = ("markdown", "html")

# `source.pre.json` 안쪽 키 → 내부 이름. 이 블록만 안쪽 이름을 v2 어휘로 옮긴다.
#   text_fields    는 이미 `body.fields` 의 내부 이름이라, 그대로 두면 같은 파일에서 같은
#                  단어가 "청크 본문을 구성할 목표필드" 와 "본문이 담긴 원천 key" 두 뜻이 된다.
#   missing_policy 의 사용자 이름은 다른 자리에서 이미 `on_missing` 이다(`source.on_missing`).
# `format` 은 뜻이 하나뿐이라 그대로 쓴다.
_PRE_JSON_TO_V1 = {
    "body_from": "text_fields",
    "on_missing": "missing_policy",
}
_PRE_JSON_PASSTHROUGH = ("format",)
REQUIRE_KEYS = frozenset({"fields"})

# ── 표기 ↔ 내부 형태 매핑은 **여기 한 벌만** 둔다 ──────────────────────────
# normalize() 와 COVERED_V1_KEYS 가 같은 표를 읽는다. 표를 두 자리에 두면 한쪽만 고쳐져
# "설정에 썼는데 값이 사라지는" 번역 결함이 생기고, 드리프트 가드도 함께 눈이 먼다.

# kind 별로 별칭 매핑이 들어가는 v1 키.
#
# document 의 원천은 LLM 응답과 **markdown front matter** 둘이다. front matter 쪽 별칭이
# 예전에는 `source.pre.markdown.front_matter.metadata_fields` 에 따로 있어서 한 필드의
# 규칙이 두 자리로 흩어졌다 — v2 가 없애려던 바로 그 문제다. 이제 `fields.<목표>.alias` 가
# 단일 선언 자리다.
#
# 옛 표기 `metadata_fields` 흡수는 여기가 아니라 **소비 지점**(`MarkdownFrontMatterSpec`)에
# 있다. v1 표기 설정은 normalize 를 아예 거치지 않으므로(`load` 가 `is_v2` 로만 갈린다)
# 여기 두면 v1 의 `metadata_fields` 가 조용히 무효가 되고, 그걸 막으려면 소비 지점에 사본이
# 또 필요하다. 두 경로가 합류하는 곳에 한 벌만 둔다(sections 의 `include: false` 와 같은 이유).
_ALIAS_BLOCK = {
    "rows": "column_map",
    "records": "key_map",
    "sections": "shared_fields",
    "document": "front_matter_map",
}

# 필드 스펙 키 → 그 값이 들어가는 v1 블록(`{목표필드: 값}` 모양이 같은 것들).
# alias/from/as 는 kind·as 값에 따라 블록이 달라져 위 표가 따로 맡는다.
_SPEC_TO_BLOCK = {
    "collect": "collect_key_map",
    "const": "constants",
    "default": "defaults",
    "values": "value_map",
    "transform": "transforms",
    "template": "derive",
    "seq": "sequence",
    "pack": "pack",
    "raw": "raw_fields",
}

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
_FIELD_BLOCKS = set(_SPEC_TO_BLOCK.values()) | set(_ALIAS_BLOCK.values())


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
        # 오타에 가장 가까운 지원 키를 붙인다. v1 경로는 config_schema 가 같은 제안을 하는데
        # v2 는 정규화가 끝난 뒤에야 그 검증기를 타므로, 여기서 제안하지 않으면 `records_key`
        # 처럼 한 글자 틀린 키가 "쓸 수 있는 키 목록"만 던지고 끝난다.
        detail = ", ".join(f"`{key}`{_suggest(key, allowed)}" for key in unknown)
        raise ConfigV2Error(
            f"{label}: {what} 에 쓸 수 없는 키가 있습니다: {detail}. "
            f"쓸 수 있는 키: {sorted(allowed)}"
        )


def _suggest(key: str, allowed) -> str:
    """오타로 보이는 키에 가장 가까운 지원 키를 한 개 제안한다(config_schema 와 같은 기준)."""
    close = difflib.get_close_matches(key, sorted(str(k) for k in allowed), n=1, cutoff=0.6)
    return f" (혹시 `{close[0]}`?)" if close else ""


def normalize(cfg: dict, *, label: str = "custom_fields") -> tuple[dict, str]:
    """v2 설정 → `(내부 형태 dict, extractor 이름)`.

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
    # 문서형만 값을 만드는 주체가 둘이다(LLM / 고객 파이썬 함수). kind 로는 갈리지 않으므로
    # `python:` 블록 유무로 정한다. 이 갈래가 없으면 파생값이 항상 llm 이라, python 설정이
    # "이 extractor 가 읽지 않는 키: file, callable" 로 막힌다 — 설정에 적은 이름은
    # `python.file` 인데 메시지에는 없는 이름이 나와 역추적이 안 됐다.
    if kind == "document" and cfg.get("python") is not None:
        extractor = "python"
    out: dict[str, Any] = {}

    _normalize_source(source, kind, out, label)
    _normalize_fields(cfg.get("fields"), kind, out, label)
    _normalize_require(cfg.get("require"), kind, out, label)
    _normalize_body(cfg.get("body"), kind, out, label)
    _normalize_llm(cfg.get("llm"), kind, out, label)
    _normalize_python(cfg.get("python"), kind, out, label)

    if cfg.get("filter") is not None:
        if kind not in ("rows", "records"):
            raise ConfigV2Error(f"{label}: filter 는 kind: rows/records 전용입니다.")
        out["filter"] = cfg["filter"]
    # 표 설명 오버라이드는 kind 와 무관하다 — 표는 문서형이든 레코드 본문이든 생긴다.
    if cfg.get("table_text_description") is not None:
        out["table_text_description"] = cfg["table_text_description"]
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
        _check_unknown(
            pre, frozenset(PRE_KEYS) | frozenset(PRE_SHARED_KEYS), label, "source.pre"
        )
        shared = {k: pre[k] for k in PRE_SHARED_KEYS if pre.get(k) is not None}
        for key in PRE_KEYS:
            block = pre.get(key)
            if key == "json":
                if block is not None:
                    out[key] = _normalize_pre_json(block, label)
                continue
            if key not in _PRE_SHARED_TARGETS or not shared:
                if block is not None:
                    out[key] = block
                continue
            if block is not None and not isinstance(block, dict):
                out[key] = block  # `markdown: false` 같은 비-dict 표기는 그대로 둔다
                continue
            # 세밀한 지정이 뭉뚱그린 지정을 덮는다 — 그 반대는 예측하기 어렵다.
            out[key] = {**shared, **(block or {})}


def _normalize_pre_json(block: Any, label: str) -> Any:
    """`source.pre.json` → 내부 형태(`JsonTextSpec` 이 읽는 이름).

    `json: false` 처럼 dict 가 아닌 값은 명시적 비활성이므로 그대로 통과시킨다 —
    판정은 소비 지점(`resolve_format_cfg`)이 markdown/html 과 같은 규칙으로 한다.
    """
    if not isinstance(block, dict):
        return block
    _check_unknown(
        block,
        frozenset(_PRE_JSON_TO_V1) | frozenset(_PRE_JSON_PASSTHROUGH),
        label,
        "source.pre.json",
    )
    if not block.get("body_from"):
        # 소비 지점(`JsonTextSpec`)도 같은 검사를 하지만 그쪽 메시지는 내부 이름
        # (`json.text_fields`)으로 나온다. 새 표기로 적은 설정은 새 이름으로 알린다.
        raise ConfigV2Error(
            f"{label}: source.pre.json.body_from 가 비어 있습니다"
            f"(본문 텍스트가 담긴 원천 key 이름 목록)."
        )
    out: dict[str, Any] = {}
    for v2_key, v1_key in _PRE_JSON_TO_V1.items():
        if block.get(v2_key) is not None:
            out[v1_key] = block[v2_key]
    for key in _PRE_JSON_PASSTHROUGH:
        if block.get(key) is not None:
            out[key] = block[key]
    return out


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
            if alias_block is None:  # _ALIAS_BLOCK 에 없는 kind (지금은 없다)
                raise ConfigV2Error(f"{where}: kind: {kind} 에는 alias 를 쓸 수 없습니다.")
            out.setdefault(alias_block, {})[target] = _require_list(
                spec["alias"], where, "alias"
            )
        if "select" in spec:
            if kind != "html":
                raise ConfigV2Error(f"{where}: select 는 kind: html 전용입니다.")
            css = spec["select"]
            if not isinstance(css, str) or not css.strip():
                raise ConfigV2Error(
                    f"{where}: select 는 CSS 선택자 문자열이어야 합니다(예: `.new-banner-headline`)."
                )
            attr = spec.get("attr")
            if attr is not None and (not isinstance(attr, str) or not attr.strip()):
                raise ConfigV2Error(f"{where}: attr 은 속성 이름 문자열이어야 합니다.")
            out.setdefault("select_map", {})[target] = {
                "css": css.strip(), "attr": attr.strip() if attr else None,
            }
        elif "attr" in spec:
            # `attr` 만 적은 설정은 원천을 지목하지 않은 것이라 값이 만들어지지 않는다.
            # 조용히 무시하면 "설정에 적었는데 비어 있다"가 된다.
            raise ConfigV2Error(f"{where}: attr 은 select 와 함께 써야 합니다.")
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
            elif spec_key == "pack":
                value = _require_list(value, where, "pack")
            out.setdefault(block, {})[target] = value


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
    """`llm:` 목록 → 문서형은 최상위 키로, 나머지는 `llm_fields` 로.

    **호출 단위는 `kind` 가 정하지 설정이 정하지 않는다.** 매퍼가 고정하고 있기 때문이다.
      · document — 문서 1회(문서 단위 추출 그 자체)
      · rows / records — 행·레코드마다 1회
      · sections — **문서 1회**. json_semantic 이 llm_fields_scope="document" 로 고정이라
        섹션 수와 무관하고, 결과가 그 파일의 모든 섹션에 같은 값으로 복사된다.
    그래서 `scope:` 를 설정으로 받지 않는다 — 받으면 sections 처럼 "kind 와 호출 단위가
    다른" 경우에 무엇을 적어야 하는지 알 수 없고, 틀리게 적을 여지만 생긴다.
    """
    items = _require_list(llm, label, "llm")
    if not items:
        return
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ConfigV2Error(f"{label}: llm[{index}] 는 object 여야 합니다.")
        if "scope" in item:
            raise ConfigV2Error(
                f"{label}: llm[{index}].scope 는 쓰지 않습니다 — 호출 단위는 source.kind 가 "
                f"정합니다(document=문서 1회 / rows·records=건별 1회 / sections=문서 1회)."
            )
        flat = _flatten_llm_item(item, f"{label}: llm[{index}]")
        if kind == "document":
            out.update(flat)
        else:
            out.setdefault("llm_fields", []).append(flat)


# `python:` 블록이 받는 키. `out` 은 v2 쪽 이름이고 v1 에서는 output_fields 다
# (그래서 아래 COVERED_V1_KEYS 에는 v1 이름 둘만 더한다).
PYTHON_KEYS = frozenset({"file", "callable", "out"})
PYTHON_V1_KEYS = frozenset({"file", "callable"})


def _normalize_python(python: Any, kind: str, out: dict, label: str) -> None:
    """`python:` → v1 의 file/callable.

    값을 만드는 주체가 LLM 이 아니라 고객 함수인 경우다. 문서 단위 추출에만 쓴다 —
    행·레코드 매핑은 원천에서 값을 그대로 꺼내므로 이 선택지가 필요 없다.
    """
    if python is None:
        return
    block = _require_dict(python, label, "python")
    if kind != "document":
        raise ConfigV2Error(f"{label}: python 은 kind: document 전용입니다.")
    if out.get("url") or out.get("model"):
        raise ConfigV2Error(
            f"{label}: llm 과 python 은 함께 쓸 수 없습니다 — 값을 만드는 주체는 하나입니다."
        )
    _check_unknown(block, PYTHON_KEYS, label, "python")
    if not block.get("file"):
        raise ConfigV2Error(f"{label}: python.file 이 필요합니다.")
    out["file"] = block["file"]
    if block.get("callable"):
        out["callable"] = block["callable"]
    # 출력 필드 이름은 llm 항목의 `out` 과 같은 뜻이다 — 값을 만드는 주체만 다르다.
    if block.get("out") is not None:
        out["output_fields"] = block["out"]


def _flatten_llm_item(item: dict, where: str) -> dict:
    """v2 의 endpoint/params/prompt 묶음을 v1 의 평평한 키로 편다."""
    flat: dict[str, Any] = {}
    for key, value in item.items():
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


# llm 문서형 설정의 최상위 키 → llm 항목 안에서의 자리. 지금은 아래 커버리지 집합만
# 이 표를 읽는다 — 새 llm 키를 코드에 더하면 여기에도 넣어야 드리프트 검사가 잡는다.
_LLM_ENDPOINT_KEYS = ("url", "api_key", "model")
_LLM_PARAM_KEYS = ("max_tokens", "temperature", "timeout", "thinking", "thinking_dialect")
_LLM_PROMPT_KEYS = {
    "system_prompt": "system", "user_prompt": "user",
    "system_prompt_file": "system_file", "user_prompt_file": "user_file",
    "variables": "variables",
}


def load(loaded: dict, *, label: str) -> tuple[dict, str | None]:
    """설정 파일 내용 → `(내부 형태, extractor 이름)`.

    매퍼의 `_load_config` 끝에서 이 함수를 한 번 거치게 하면, 그 아래 코드는 표기를
    신경 쓸 필요가 없다.

    설정이 비어 있으면(`config_file` 미지정) 번역할 것이 없으므로 그대로 통과시킨다.
    그 밖에 `schema: v2` 가 없으면 **기동을 막는다** — 폐기된 v1 표기를 조용히 다른
    해석 모드로 받으면, `schema` 줄의 사소한 사고가 에러가 아니라 스키마 전환이 된다.
    """
    if not loaded:
        return {}, None
    if not is_v2(loaded):
        raise ConfigV2Error(
            f"{label}: 최상위에 `schema: v2` 가 없습니다. v1 표기는 더 이상 지원하지 "
            f"않습니다(필드 규칙은 `fields.<목표>` 한 자리에 모읍니다)."
        )
    return normalize(loaded, label=label)


# v2 가 표현할 수 있는 v1 키 전체. 위 단일 표에서 파생하므로 표를 고치면 여기도 따라온다.
# `tests/unit/test_config_v2_unit.py` 가 이 집합이 config_schema.EXTRACTOR_KEYS 를 덮는지
# 지켜, v1 에 새 키를 넣고 v2 를 잊는 드리프트를 배포 전에 잡는다.
COVERED_V1_KEYS = (
    _FIELD_BLOCKS
    | {v1 for v1, _kinds in _SOURCE_TO_V1.values()}
    | set(_BODY_TO_V1.values())
    | {"required", "required_shared_fields", "llm_fields", "filter"}
    | set(_LLM_ENDPOINT_KEYS) | set(_LLM_PARAM_KEYS) | set(_LLM_PROMPT_KEYS)
    | {"output_fields", "parser", "pages", "template", "table_text_description", "prompt"}
    | PYTHON_V1_KEYS
    | {"select_map"}
    | set(PRE_KEYS)
)
