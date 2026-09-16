"""설정이 이름으로 부르는 고객 파이썬 파일 로더.

custom_fields 설정에서 파이썬을 꽂는 자리가 둘이고, 둘 다 같은 규칙을 쓴다.

    parser: {type: python, file: ..., callable: ...}   LLM 출력 해석
    extractor: python + file/callable                  값 추출 자체

규칙은 하나뿐이다 — **파일은 config yaml 이 있는 폴더 아래**여야 한다. 설정 파일이 가리킬
수 있는 범위를 벗어난 경로는 거부한다(운영 서버에서 임의 경로 실행을 막는다).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Callable


def load_callable(base_dir: Path, file: str, callable_name: str, *, label: str) -> Callable:
    """`base_dir` 아래 파이썬 파일에서 함수 하나를 가져온다.

    오류 메시지에 `label` 을 실어 어느 설정이 잘못됐는지 바로 보이게 한다.
    """
    if not file:
        raise ValueError(f"{label}: 파이썬을 쓰려면 file 값이 필요합니다.")

    base = Path(base_dir).resolve()
    path = (base / file).resolve()
    try:
        path.relative_to(base)
    except ValueError as exc:
        raise ValueError(
            f"{label}: 파일 경로가 허용 범위를 벗어났습니다: {path} (기준: {base})"
        ) from exc
    if not path.exists():
        raise FileNotFoundError(f"{label}: 파일이 없습니다: {path}")

    module_name = f"genos_plugin_{abs(hash(str(path)))}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"{label}: 모듈 로딩 실패: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    fn = getattr(module, callable_name, None)
    if not callable(fn):
        raise TypeError(
            f"{label}: 호출 가능한 {callable_name!r} 을 {path.name} 에서 찾지 못했습니다."
        )
    return fn
