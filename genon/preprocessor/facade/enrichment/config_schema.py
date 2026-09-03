"""custom_fields yaml 의 **지원 키를 extractor 별로 선언**하고 기동 시 대조한다.

왜 필요한가 — 지금까지 설정 오기입은 두 가지 방식으로 조용히 사라졌다.

1. **모르는 키는 아무도 안 본다.** 검증기는 알려진 키를 이름으로 꺼내 타입·참조 정합만
   봤을 뿐, `set(cfg) - 지원키` 를 훑는 곳이 없었다. `column_maps`(오타 s) 한 글자로
   매핑 전체가 사라져도 에러도 경고도 없다.
2. **extractor 가 안 읽는 키도 검증은 통과한다.** `json_semantic` 설정에 `text_fields` 를
   올바른 필드명으로 적으면 검증을 통과하고 그대로 무시된다. 반대로 이름을 틀리면
   `chunk_prefix_fields` 처럼 **읽지도 않는 키 때문에 기동이 실패**하기도 했다.

그래서 "이 extractor 가 읽는 키" 를 한 곳에 적고, 그 목록으로 두 가지를 판정한다.
  · 어느 extractor 도 모르는 키 → **기동 실패**(오타로 본다. 가장 가까운 이름을 제안한다)
  · 다른 extractor 의 키를 잘못 쓴 경우 → **기동 실패**(읽히지 않으므로 설정이 무효다)

여기 선언한 목록이 곧 "이 extractor 로 무엇을 설정할 수 있는가" 의 단일 출처다.
새 키를 코드에 추가하면 반드시 여기에도 넣어야 하며, 넣지 않으면 그 키를 쓴 설정이
기동에 실패해 누락이 바로 드러난다.
"""

from __future__ import annotations

import difflib

# 등록 블록(parser_processor_config.yaml 의 `- custom_fields:`)에서 넘어오는 배선 키.
# custom_field yaml 안에 적을 값이 아니라 매퍼/enricher 생성자의 인자다.
WIRING_KEYS = frozenset({
    "enable", "doc_type", "extractor", "config_file", "resource_path",
    # 아래 두 개는 enricher 가 아니라 parser 의 포맷 전처리가 소비한다.
    "json", "markdown", "html",
})

# 레코드형 3종이 공유하는 키(값 조립 → 본문 조립 파이프라인).
_RECORD_COMMON = frozenset({
    "required", "nulls", "defaults", "constants",
    "value_map", "transforms",
    "text_from", "html_text_fields", "json_text_fields",
    "llm_fields",
    "text_fields", "split", "chunk_prefix_fields", "field_labels",
})

# extractor 별 지원 키. 값은 "그 extractor 의 매퍼/enricher 가 실제로 읽는 키" 다.
EXTRACTOR_KEYS: dict[str, frozenset[str]] = {
    "tabular_mapping": _RECORD_COMMON | {"column_map", "row_merge"},
    "json_mapping": _RECORD_COMMON | {"records", "key_map", "collect_key_map", "missing_policy"},
    # json_semantic 은 레코드형과 파이프라인이 다르다 — 값 변환·본문 조립 키를 읽지 않고
    # 본문은 섹션 워커가 만든다. 공유 키 중 defaults/constants/llm_fields 만 쓴다.
    "json_semantic": frozenset({
        "shared_fields", "sections", "ignore_keys",
        "required_shared_fields", "missing_policy",
        "defaults", "constants", "llm_fields", "field_labels",
    }),
    # 문서 단위 LLM 추출. 값 매핑이 없고 프롬프트·연결·출력필드가 중심이다.
    "llm": frozenset({
        "url", "api_key", "model", "max_tokens", "temperature", "timeout",
        "system_prompt", "user_prompt", "system_prompt_file", "user_prompt_file", "prompt",
        "output_fields", "constants", "parser", "pages", "variables", "template",
        "thinking", "thinking_dialect", "table_text_description",
        "body_fields", "chunk_prefix_fields", "first_chunk_fields", "field_labels",
    }),
}

# 코드가 쓰는 정규 이름으로 접기 위한 별칭표(custom_fields_enricher 의 extractor 집합과 같다).
_EXTRACTOR_ALIASES = {
    "tabular": "tabular_mapping",
    "column_mapping": "tabular_mapping",
    "json_records": "json_mapping",
    "document_llm": "llm",
}

ALL_KNOWN_KEYS = frozenset().union(*EXTRACTOR_KEYS.values()) | WIRING_KEYS


def canonical_extractor(extractor: str | None) -> str:
    """별칭을 정규 이름으로 접는다. 모르는 값은 그대로 돌려준다(호출부가 판정)."""
    name = str(extractor or "llm").strip().lower()
    return _EXTRACTOR_ALIASES.get(name, name)


def _suggest(key: str, candidates: frozenset[str]) -> str:
    """오타로 보이는 키에 가장 가까운 지원 키를 한 개 제안한다."""
    close = difflib.get_close_matches(key, sorted(candidates), n=1, cutoff=0.75)
    return f" (혹시 `{close[0]}`?)" if close else ""


def validate_known_keys(cfg: dict, *, label: str, extractor: str | None) -> None:
    """설정 최상위 키가 이 extractor 가 읽는 키인지 대조한다.

    두 종류를 나눠 알린다 — 오타인지, 다른 extractor 의 키를 가져다 쓴 것인지에 따라
    고칠 방법이 다르기 때문이다.
    """
    name = canonical_extractor(extractor)
    supported = EXTRACTOR_KEYS.get(name)
    if supported is None:  # 모르는 extractor 는 각 매퍼 생성자가 이미 거부한다.
        return

    allowed = supported | WIRING_KEYS
    unexpected = sorted(k for k in (cfg or {}) if str(k) not in allowed)
    if not unexpected:
        return

    typos = [k for k in unexpected if k not in ALL_KNOWN_KEYS]
    misplaced = [k for k in unexpected if k in ALL_KNOWN_KEYS]

    lines = []
    if typos:
        detail = ", ".join(f"`{k}`{_suggest(k, allowed)}" for k in typos)
        lines.append(f"모르는 키: {detail}")
    if misplaced:
        owners = {
            k: sorted(x for x, keys in EXTRACTOR_KEYS.items() if k in keys)
            for k in misplaced
        }
        detail = ", ".join(f"`{k}`(→ {'/'.join(owners[k])} 전용)" for k in misplaced)
        lines.append(f"이 extractor 가 읽지 않는 키: {detail}")

    raise ValueError(
        f"{label}: extractor '{name}' 설정에 쓸 수 없는 키가 있습니다. "
        + " / ".join(lines)
        + f". 지원 키: {sorted(supported)}"
    )
