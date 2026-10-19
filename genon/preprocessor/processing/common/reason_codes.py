"""오류 사유 코드(#528).

오류 응답의 `reason_code` 로 실리는 값이다. 형식은 `<단계>_<분류문자><3자리 번호>` 이며
분류문자는 D(원천데이터)·P(전처리)·S(시스템)이다. 코드표 정본은
`manual/custom_fields_exceptions.md` 의 "사유 코드" 절이다.

원인 예외가 코드를 실어 보내면 감싸는 자리는 `reason_of` 로 그 값을 우선한다. 같은 모듈이
두 import 경로로 올라와 클래스 객체가 갈릴 수 있으므로 isinstance 대신 속성으로 읽는다.
"""

from __future__ import annotations

import errno
from typing import Optional


class CodedValueError(ValueError):
    """사유 코드를 실은 ValueError. 기존 `except ValueError` 절은 그대로 잡는다."""

    def __init__(self, message: str, reason_code: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def reason_of(exc: BaseException, default: Optional[str] = None) -> Optional[str]:
    """원인 예외가 실은 사유 코드. 없으면 default."""
    return getattr(exc, "reason_code", None) or default


# 다시 시도해도 결과가 같은 파일 오류(없는 경로, 권한 거부).
_PERMANENT_ERRNOS = frozenset({errno.ENOENT, errno.ENOTDIR, errno.EISDIR, errno.EACCES, errno.EPERM})


def os_error_reason(exc: OSError, prefix: str) -> tuple[str, str]:
    """OSError 를 (사유 코드, 성격)으로 나눈다. prefix 는 `PARSE` 또는 `CHUNK` 이다."""
    if getattr(exc, "errno", None) in _PERMANENT_ERRNOS:
        return f"{prefix}_S003", "permanent"
    return f"{prefix}_S103", "transient"
