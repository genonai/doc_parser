"""오류 사유 코드 헬퍼(#528).

원인 예외 쪽(processing/common/reason_codes.py)과 응답 쪽(src/utils.py)의 순수 함수만
확인한다. 실물 샘플로 만든 오류 입력이 응답에 어떤 코드로 실리는지는
tools/code_serving/error_response_check.py 가 루트 main.py 를 띄워 확인한다.
"""

import errno

import pytest

from genon.preprocessor.processing.common.reason_codes import os_error_reason
from utils import reason_code_of, reason_fields


@pytest.mark.unit
@pytest.mark.parametrize("code,expected", [
    (errno.ENOENT, ("PARSE_S003", "permanent")),
    (errno.EACCES, ("PARSE_S003", "permanent")),
    (errno.EIO, ("PARSE_S103", "transient")),
    (None, ("PARSE_S103", "transient")),
])
def test_os_error_reason(code, expected):
    """없는 경로·권한 거부는 다시 해도 같고, 그 밖의 입출력 오류는 일시 장애로 본다."""
    assert os_error_reason(OSError(code, "x"), "PARSE") == expected


@pytest.mark.unit
@pytest.mark.parametrize("exc,expected", [
    (type("E", (Exception,), {"reason_code": "CHUNK_D001"})(),
     {"reason_code": "CHUNK_D001", "error_category": "data"}),
    (type("E", (Exception,), {"reason_code": "PARSE_S101"})(),
     {"reason_code": "PARSE_S101", "error_category": "system"}),
    (ValueError("x"), {"reason_code": "PARSE_P999", "error_category": "preprocess"}),
])
def test_reason_fields(exc, expected):
    """예외의 사유 코드가 분류와 함께 실리고, 코드가 없으면 미분류 코드가 실린다."""
    assert reason_fields(reason_code_of(exc, "PARSE")) == expected
