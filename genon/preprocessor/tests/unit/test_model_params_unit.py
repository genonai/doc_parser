"""모델 요청 파라미터 공용 해석 단위 테스트.

`processing/common/model_params.py` 는 "섹션마다 설정할 수 있는 옵션이 다르다" 를 없애려고
만든 단일 규칙이다. 그래서 여기서 고정해야 하는 것은 편의 기능이 아니라 규칙 자체다.

  · 설정에 적은 키만 담는가 (안 적으면 기존 현장의 요청이 그대로여야 한다)
  · 임의 키 통로(`params`)가 이름 있는 키를 이기는가
  · 호출부의 typed 기본값과 설정값 중 설정값이 이기는가

stdlib 만 의존하므로 GitHub CI(내부망 미접근)에서도 돈다.
"""

import pytest

from processing.common.model_params import (
    GENERATION_KEYS,
    build_chat_payload,
    collect_generation_params,
    resolve_headers,
)

pytestmark = pytest.mark.unit


# ── collect_generation_params ─────────────────────────────────────────────────

def test_collects_only_keys_written_in_config():
    """설정에 적은 키만 담는다. 코드 기본값을 채우면 호출부의 typed 값을 덮어쓴다."""
    assert collect_generation_params({}) == {}
    assert collect_generation_params({"temperature": 0.3}) == {"temperature": 0.3}


def test_supports_the_full_generation_key_set():
    """다섯 키가 한 벌로 지원된다 — 섹션마다 되는 키가 다르던 것을 없애는 것이 목적이다."""
    cfg = {
        "temperature": 0.2, "top_p": 0.9, "repetition_penalty": 1.1,
        "max_tokens": 4096, "seed": 33,
    }
    assert collect_generation_params(cfg) == cfg
    assert set(GENERATION_KEYS) == set(cfg)


def test_values_are_coerced_to_number_types():
    """yaml 이 문자열로 준 값도 숫자로 보낸다(게이트웨이가 타입을 가린다)."""
    got = collect_generation_params({"temperature": "0.5", "max_tokens": "100"})
    assert got == {"temperature": 0.5, "max_tokens": 100}
    assert isinstance(got["max_tokens"], int)


def test_unparsable_value_is_dropped_not_sent():
    """해석 못 하는 값은 담지 않는다. 그대로 보내면 게이트웨이가 400 으로 막는다."""
    assert collect_generation_params({"temperature": "뜨겁게", "top_p": 0.9}) == {"top_p": 0.9}


def test_params_passthrough_wins_over_named_key():
    """`params` 는 '검사하지 말고 이대로 보내라' 는 탈출구라 이름 있는 키를 이긴다."""
    got = collect_generation_params({"temperature": 0.1, "params": {"temperature": 0.9}})
    assert got["temperature"] == 0.9


def test_params_carries_keys_the_code_does_not_know():
    """코드가 모르는 키도 그대로 실린다 — 새 키가 생길 때마다 코드를 고치지 않기 위함이다."""
    got = collect_generation_params({"params": {"presence_penalty": 0.5, "stop": ["END"]}})
    assert got == {"presence_penalty": 0.5, "stop": ["END"]}


def test_non_mapping_params_is_ignored():
    assert collect_generation_params({"params": [1, 2], "seed": 7}) == {"seed": 7}


# ── resolve_headers ───────────────────────────────────────────────────────────

def test_headers_config_is_merged_and_content_type_defaulted():
    got = resolve_headers({"headers": {"X-Tenant": "kb"}}, "")
    assert got["X-Tenant"] == "kb"
    assert got["Content-Type"] == "application/json"


def test_api_key_becomes_bearer_when_not_set_by_config():
    assert resolve_headers({}, "secret")["Authorization"] == "Bearer secret"


def test_config_authorization_is_not_overwritten_by_api_key():
    """Bearer 가 아닌 인증을 쓰는 게이트웨이가 있다. 설정이 적었으면 그것이 최종이다."""
    got = resolve_headers({"headers": {"Authorization": "Basic zzz"}}, "secret")
    assert got["Authorization"] == "Basic zzz"


def test_no_authorization_without_api_key():
    assert "Authorization" not in resolve_headers({}, "")


# ── build_chat_payload ────────────────────────────────────────────────────────

def test_typed_defaults_are_included_and_none_is_dropped():
    payload = build_chat_payload(
        model="m", messages=[{"role": "user", "content": "x"}],
        temperature=0.0, max_tokens=None,
    )
    assert payload["model"] == "m"
    assert payload["temperature"] == 0.0
    assert "max_tokens" not in payload      # None 은 키 자체를 만들지 않는다


def test_config_params_override_typed_defaults():
    """호출부의 코드 기본값보다 설정에 적은 값이 우선한다."""
    payload = build_chat_payload(
        model="m", messages=[], temperature=0.0,
        params={"temperature": 0.7, "seed": 1},
    )
    assert payload["temperature"] == 0.7
    assert payload["seed"] == 1


def test_thinking_kwargs_key_absent_when_empty():
    """빈 dict 를 보내면 기본값 대신 빈 설정으로 읽는 게이트웨이가 있다."""
    assert "chat_template_kwargs" not in build_chat_payload(
        model="m", messages=[], thinking_kwargs={}
    )
    payload = build_chat_payload(
        model="m", messages=[], thinking_kwargs={"enable_thinking": False}
    )
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}


def test_empty_model_falls_back_to_literal_model():
    """기존 호출부들이 모두 쓰던 폴백이다(설정 model 이 비어도 요청은 나간다)."""
    assert build_chat_payload(model="", messages=[])["model"] == "model"
