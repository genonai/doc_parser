"""골든 대조용 정규화 — 실행마다 흔들리는 필드를 자리표시자로 바꾼다.

정규화 대상은 추정이 아니라 실측으로 정한다. 같은 코드로 2회 실행해(`--noise`)
실제로 달라진 필드만 여기에 넣는다. 자동 확장은 하지 않는다 — 회귀를 노이즈로 삼킨다.

골든 하네스(파일 대조)와 A/B 대조기(in-process dict 대조)가 이 모듈 하나를 공유한다.
두 벌로 두면 곧 갈라진다.

주의: A/B 대조기는 정규화를 무조건 걸면 안 된다. 같은 프로세스에서 연달아 도는 A/B 는
reg_date 가 같을 수 있어 정규화 없이도 통과하는데, 무조건 정규화하면 "reg_date 생성
로직이 통째로 사라진 회귀" 를 못 잡는다. 정규화 전/후를 둘 다 비교한다.
"""

from __future__ import annotations

import difflib
import json
from typing import Any

# 실측으로 확인된 유일한 노이즈. 늘리기 전에 --noise 로 근거를 만든다.
VOLATILE_FIELDS: tuple[str, ...] = ("reg_date",)

PLACEHOLDER = "<volatile>"


def normalize(obj: Any, volatile: tuple[str, ...] = VOLATILE_FIELDS) -> Any:
    """dict/list 를 재귀적으로 훑어 volatile 키의 값을 자리표시자로 바꾼다.

    키를 지우지 않고 값만 바꾼다. 키 자체가 사라진 회귀는 그대로 차이로 드러나야 한다.
    """
    if isinstance(obj, dict):
        return {
            k: (PLACEHOLDER if k in volatile else normalize(v, volatile))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [normalize(v, volatile) for v in obj]
    return obj


def dumps(obj: Any) -> str:
    """대조용 직렬화. 키 순서는 산출 그대로 둔다(순서 변화도 회귀다)."""
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False)


def diff_lines(a: Any, b: Any, a_label: str, b_label: str, context: int = 2) -> list[str]:
    """정규화된 두 산출의 unified diff. 같으면 빈 목록."""
    left = dumps(a).splitlines()
    right = dumps(b).splitlines()
    if left == right:
        return []
    return list(
        difflib.unified_diff(left, right, fromfile=a_label, tofile=b_label, n=context, lineterm="")
    )


def _walk(obj: Any, path: str, out: dict[str, Any]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            _walk(v, f"{path}.{k}" if path else str(k), out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _walk(v, f"{path}[{i}]", out)
    else:
        out[path] = obj


def noise_candidates(a: Any, b: Any) -> list[str]:
    """정규화 없이 두 산출을 훑어 값이 다른 경로의 **필드명** 목록을 뽑는다.

    출력 전용이다. 여기서 나온 이름을 VOLATILE_FIELDS 에 자동으로 넣지 않는다 —
    사람이 보고 판단해서 넣는다.
    """
    left: dict[str, Any] = {}
    right: dict[str, Any] = {}
    _walk(a, "", left)
    _walk(b, "", right)
    names: dict[str, int] = {}
    for path in set(left) | set(right):
        if left.get(path, _MISSING) == right.get(path, _MISSING):
            continue
        leaf = path.rsplit(".", 1)[-1].split("[", 1)[0] or path
        names[leaf] = names.get(leaf, 0) + 1
    return [f"{name} ({count}곳)" for name, count in sorted(names.items(), key=lambda kv: -kv[1])]


class _Missing:
    def __repr__(self) -> str:  # pragma: no cover - 진단용
        return "<missing>"


_MISSING = _Missing()
