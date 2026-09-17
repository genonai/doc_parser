"""model_preset.py — 모델 설정 프리셋 해석.

같은 모델 서빙 주소와 api_key 가 설정 파일 곳곳에 되풀이되던 것을 한 자리로 모은다.
최상위 `model_presets:` 에 모델 한 벌을 이름 붙여 두고, 어느 블록에서나
`model_preset: <이름>` 한 줄로 그 값을 끌어 쓴다.

    model_presets:
      기본:
        url: "http://.../v1/chat/completions"
        api_key: ""
        model: "model"
        temperature: 0.0

    enrichment:
      - toc:
          model_preset: 기본
          temperature: 0.2      # 이 블록만 다르게 — 직접 쓴 값이 항상 이긴다

해석은 `config_parse.load_config` 한 곳에서 끝난다. 설정을 읽는 쪽(EnrichmentConfig,
PageDescriptionOptions, whisper, guardrail …)은 프리셋의 존재를 모르고 지금까지와 똑같이
평평한 dict 를 본다.

이름을 `models` 로 하지 않은 이유는 그 키가 docling 모델 경로(`models.artifacts_path`)로
이미 쓰이고 있어서다. `endpoints` 도 layout 과 custom_fields v2 가 다른 뜻으로 점유하고 있다.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Optional

_log = logging.getLogger(__name__)

# 최상위 프리셋 정의 블록 / 블록 안의 참조 키.
PRESETS_KEY = "model_presets"
REF_KEY = "model_preset"

# 프리셋에 실려도 주입하지 않는 키. 모델의 성질이 아니라 "이 블록을 어떻게 쓸지" 를 정하는
# 배선 값들이다. 특히 `enable` 이 새어 나가면 그 프리셋을 참조하는 모든 섹션이 함께 켜진다.
BLOCKED_KEYS = frozenset({
    "enable", "enabled",
    REF_KEY, PRESETS_KEY,
    "config_file", "doc_type",
})


class ModelPresetError(ValueError):
    """프리셋 정의나 참조가 잘못됐을 때. 기동 시점에 드러난다."""


def get_presets(cfg: Any, *, label: str = "", strict: bool = True) -> dict:
    """최상위 `model_presets:` 를 읽어 {이름: 설정} 으로 돌려준다. 없으면 빈 dict.

    형태가 틀리면 strict 에서 기동을 막는다 — 프리셋이 통째로 무시되면 연결 정보 없이
    호출이 나가고, 그 실패는 기동이 아니라 문서 처리 중에 터진다.
    """
    raw = cfg.get(PRESETS_KEY) if isinstance(cfg, dict) else None
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        return _fail(
            f"{label}: `{PRESETS_KEY}` 는 {{이름: 설정}} 매핑이어야 합니다 "
            f"(받은 것: {type(raw).__name__}).",
            strict=strict,
            fallback={},
        )
    presets: dict = {}
    for name, value in raw.items():
        if not isinstance(value, dict):
            return _fail(
                f"{label}: `{PRESETS_KEY}.{name}` 은 매핑이어야 합니다 "
                f"(받은 것: {type(value).__name__}).",
                strict=strict,
                fallback={},
            )
        presets[str(name)] = value
    return presets


def expand_block(
    block: dict,
    presets: dict,
    *,
    label: str = "",
    strict: bool = True,
    allowed: Optional[Iterable[str]] = None,
) -> dict:
    """블록 하나의 `model_preset:` 참조를 펼친다. 참조가 없으면 원본을 그대로 돌려준다.

    병합 규칙은 셋이다.
      1. 블록에 이미 있는 키는 건드리지 않는다(직접 쓴 값이 항상 이긴다).
      2. 양쪽이 모두 dict 인 키(`params`/`headers`/`variables` …)는 한 겹 병합한다.
         프리셋의 `params` 를 통째로 날리지 않고 블록이 더한 항목만 얹는다.
      3. `BLOCKED_KEYS` 는 주입하지 않는다.

    Args:
        allowed: 주어지면 이 집합에 있는 키만 주입한다. 받는 쪽이 모르는 키를 기동 실패로
            처리하는 자리(custom_fields 자식 설정)에서 쓴다. None 이면 거르지 않는다.
    """
    if not isinstance(block, dict) or REF_KEY not in block:
        return block

    name = block.get(REF_KEY)
    merged = {k: v for k, v in block.items() if k != REF_KEY}
    where = f"{label}.{REF_KEY}" if label else REF_KEY

    if not isinstance(name, str) or not name.strip():
        return _fail(
            f"{where}: 프리셋 이름은 비어 있지 않은 문자열이어야 합니다 (받은 것: {name!r}).",
            strict=strict,
            fallback=merged,
        )
    name = name.strip()
    if name not in presets:
        known = ", ".join(sorted(presets)) or "(정의된 프리셋 없음)"
        return _fail(
            f"{where}: `{name}` 프리셋이 `{PRESETS_KEY}` 에 없습니다. 정의된 이름: {known}",
            strict=strict,
            fallback=merged,
        )

    allowed_keys = frozenset(allowed) if allowed is not None else None
    for key, value in presets[name].items():
        if key in BLOCKED_KEYS:
            _log.warning(
                f"[model_preset] `{PRESETS_KEY}.{name}.{key}` 는 프리셋에 둘 수 없는 "
                f"배선 키라 무시합니다. 이 값은 쓰는 블록에 직접 적으세요."
            )
            continue
        if allowed_keys is not None and key not in allowed_keys:
            _log.debug(
                f"[model_preset] {where}: `{key}` 는 이 설정이 읽지 않는 키라 건너뜁니다."
            )
            continue
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = {**value, **current}
        elif key not in merged:
            merged[key] = value
    return merged


def iter_refs(node: Any, *, path: str = ""):
    """설정 안의 `model_preset:` 참조를 (자리, 이름) 으로 훑는다.

    배포 전 설정 점검(`examples/config_precheck`)이 "어디서 어떤 이름을 불렀는가" 를
    세려고 쓴다. 참조를 찾는 규칙을 스크립트가 다시 구현하면 이 모듈과 갈린다.
    """
    if isinstance(node, dict):
        if REF_KEY in node:
            yield (path or "(최상위)", node[REF_KEY])
        for key, value in node.items():
            if key in (PRESETS_KEY, REF_KEY):
                continue
            yield from iter_refs(value, path=f"{path}.{key}" if path else str(key))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from iter_refs(item, path=f"{path}[{index}]")


def apply(cfg: Any, *, label: str = "", strict: bool = True) -> Any:
    """설정 전체를 훑어 `model_preset:` 참조를 펼친 새 dict 를 돌려준다.

    `model_presets` 정의 블록 자신은 훑지 않는다 — 프리셋 안의 키가 다시 블록으로 취급되면
    프리셋이 프리셋을 참조하는 형태가 되어 해석 순서가 생긴다.
    """
    if not isinstance(cfg, dict):
        return cfg
    presets = get_presets(cfg, label=label, strict=strict)
    if not presets and not _has_ref(cfg):
        return cfg
    return _walk(cfg, presets, label=label, strict=strict, top=True)


# ── module-private ────────────────────────────────────────────────────────────

def _fail(message: str, *, strict: bool, fallback):
    """설정 오기입 처리. strict 면 기동을 막고, 아니면 경고만 남기고 원본으로 간다."""
    if strict:
        raise ModelPresetError(message)
    _log.warning(f"[model_preset] {message} 해당 블록은 그대로 둡니다.")
    return fallback


def _has_ref(node: Any) -> bool:
    """`model_preset` 참조가 하나라도 있는지. 프리셋 정의 없이 쓴 오타를 잡기 위함."""
    return next(iter_refs(node), None) is not None


def _walk(node: Any, presets: dict, *, label: str, strict: bool, top: bool = False) -> Any:
    if isinstance(node, dict):
        expanded = expand_block(node, presets, label=label, strict=strict)
        out = {}
        for key, value in expanded.items():
            if top and key == PRESETS_KEY:
                out[key] = value
                continue
            child = f"{label}.{key}" if label else str(key)
            out[key] = _walk(value, presets, label=child, strict=strict)
        return out
    if isinstance(node, list):
        return [
            _walk(item, presets, label=f"{label}[{index}]", strict=strict)
            for index, item in enumerate(node)
        ]
    return node
