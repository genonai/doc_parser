"""예외 → Temporal ApplicationError. 재시도 성격 판정은 여기 한 곳에서 한다.

    즉시 실패(non_retryable)  error_type == "permanent" · 입력 오류(같은 입력이면 늘 실패)
    재시도                    error_type == "transient" / "timeout" · NFS 순단 errno · 그 밖

`error_type` 은 core 가 GenosServiceException 에 싣는 값이다(processing/core/errors.py). 없으면
원인 체인(`__cause__`)을 따라간다 — 파일 읽기·JSON 입력 오류가 `error_type` 없이 감싸져 온다
(processing/core/parser.py 의 JSON 입력 경로). `OSError` 는 errno 로 가른다: NFS 순단은 재시도,
경로·권한·용량은 영구. 표는 파이프라인 app/common/pathdiag.py `TRANSIENT_ERRNOS` 와 같다.
설정 오류도 영구다 — YAML 문법 오류(`config_parse.ConfigSyntaxError`, 감싸지 않은 `yaml.YAMLError`)와
모델 프리셋 오류(`ModelPresetError`)는 같은 설정이면 늘 실패한다. ConfigSyntaxError·ModelPresetError 는
ValueError 라서 입력 오류로 잡히고, 다른 로더가 그대로 올린 `yaml.YAMLError` 는 따로 본다.

사유 코드(#528)는 `ApplicationError.details[0]` 로 싣는다(#589) — `reason_code`·`error_category`·
`error_kind`. 호출 측(pipeline)은 다시 판정하지 않고 받아 적는다. 원인 체인에 사유 코드가 없으면
`reason_code`·`error_category` 는 None 이다(HTTP 응답의 미분류 코드 `*_P999` 를 지어내지 않는다).
"""
from __future__ import annotations

import asyncio
import errno

import yaml
from temporalio.exceptions import ApplicationError

from genon.preprocessor.processing.common.reason_codes import category_of, reason_of
from genon.preprocessor.processing.core.errors import GenosServiceException

TRANSIENT_ERRNOS = frozenset({
    errno.ESTALE, errno.EIO, errno.ENOTCONN, errno.ETIMEDOUT, errno.ECONNRESET,
    errno.ECONNABORTED, errno.ENETDOWN, errno.ENETUNREACH, errno.EHOSTDOWN,
    errno.EHOSTUNREACH, errno.EAGAIN, errno.EBUSY,
})
_INPUT_OS_EXC = (FileNotFoundError, IsADirectoryError, NotADirectoryError, PermissionError)
_INPUT_EXC = (ValueError, KeyError, TypeError, IndexError, yaml.YAMLError)


def _chain(exc: BaseException):
    seen = set()
    while exc is not None and id(exc) not in seen and len(seen) < 10:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__


def classify(exc: BaseException) -> tuple[str, bool]:
    """예외 → (type, non_retryable)."""
    for e in _chain(exc):
        kind = getattr(e, "error_type", None)
        if isinstance(e, (TimeoutError, asyncio.TimeoutError)) or kind == "timeout":
            return "timeout", False
        if kind == "transient":
            return "transient", False
        if kind == "permanent":
            return "permanent", True
        if isinstance(e, OSError) and e.errno is not None:
            return ("transient", False) if e.errno in TRANSIENT_ERRNOS else ("permanent", True)
        if isinstance(e, _INPUT_OS_EXC + _INPUT_EXC):
            return "permanent", True
    return type(exc).__name__, False


def reason_details(exc: BaseException, kind: str) -> dict:
    """`ApplicationError.details[0]` — 원인 체인에서 처음 나오는 사유 코드와 그 범주. `error_kind` 는 `type` 과 같다."""
    code = next((c for c in (reason_of(e) for e in _chain(exc)) if c), None)
    return {"reason_code": code, "error_category": category_of(code), "error_kind": kind}


LINE_SEP = " / "


def one_line(message: str) -> str:
    """여러 줄 메시지를 한 줄로 — 줄마다 공백을 정리하고 줄 경계는 `LINE_SEP` 로 남긴다(pipeline 로그와 같은 표기).
    단건 CLI 는 이 구분자로 다시 나눠 콘솔에 줄마다 찍는다."""
    return LINE_SEP.join(" ".join(line.split()) for line in str(message).splitlines() if line.strip())


def to_application_error(exc: BaseException, where: str = "") -> ApplicationError:
    """메시지는 한 줄이고 **사유가 맨 앞**이다. 어디서 났는지(where — 액티비티·파일)는 뒤 괄호에 둔다.

    이 메시지는 Studio 콘솔(줄당 220자)과 GenOS 적재 상태(`document_upsert.error_message`, pipeline 이
    `[사유코드/범주] ` 를 앞에 붙인다)에 그대로 나가고 둘 다 앞부분만 보인다. 예전 형식
    `parse /nfs/…/a.pdf: GenosServiceException: …` 은 경로·클래스 이름이 사유를 밀어냈다.
    처리기가 만든 GenosServiceException 은 클래스 이름을 빼고, 그 밖의 예외(KeyError 등)는 이름이
    뜻을 보태므로 남긴다.
    """
    msg = getattr(exc, "error_msg", None) or str(exc) or type(exc).__name__
    msg = one_line(msg)                          # 여러 줄 원문(YAML·pydantic 오류 등)도 한 줄로
    if not isinstance(exc, GenosServiceException):
        msg = f"{type(exc).__name__}: {msg}"
    if where:
        msg = f"{msg} ({where})"
    kind, non_retryable = classify(exc)
    return ApplicationError(msg, reason_details(exc, kind), type=kind, non_retryable=non_retryable)
