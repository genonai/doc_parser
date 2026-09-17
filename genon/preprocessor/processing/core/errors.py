"""facade 공용 예외.

여기로 모으기 전에는 활성 경로 facade 2종이 각자 같은 클래스를 들고 있었다.
core 가 예외를 던지는 이상 한 벌이어야 한다. facade 가 던진 로컬 예외는
main.py 의 제네릭 핸들러가 받는다.
"""

from __future__ import annotations

from typing import Optional


class GenosServiceException(Exception):
    def __init__(self, error_code: str, error_msg: Optional[str] = None,
                 msg_params: Optional[dict] = None,
                 *, stage: Optional[str] = None, error_type: Optional[str] = None) -> None:
        self.code = 1
        self.error_code = error_code
        self.error_msg = error_msg or "GenOS Service Exception"
        self.msg_params = msg_params or {}
        # #329: 실패 단계(stage)와 성격(error_type: transient/permanent/timeout).
        self.stage = stage
        self.error_type = error_type

    def __repr__(self) -> str:
        return f"GenosServiceException(code={self.code!r}, errMsg={self.error_msg!r})"
