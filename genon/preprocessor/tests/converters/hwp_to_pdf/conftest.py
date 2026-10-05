"""hwp_to_pdf 단위 테스트 공통 픽스처."""
from __future__ import annotations

import pytest

from genon.preprocessor.processing.converters.hwp_to_pdf import config as cfg


@pytest.fixture(autouse=True)
def _restore_availability():
    """테스트가 바꾼 `_AVAILABILITY` 를 테스트마다 원래 값으로 되돌린다(이슈 #459).

    이 폴더의 테스트는 모듈 전역 dict 에 가용성 판정을 직접 대입한다. 복원하지 않으면 같은
    세션에서 뒤에 실행되는 smoke·regression 테스트가 backend 가 없다고 판정해 skip 된다.
    smoke·regression 테스트가 같은 dict 객체를 import 로 잡고 있으므로 객체를 바꾸지 않고
    내용만 되돌린다.
    """
    saved = dict(cfg._AVAILABILITY)
    yield
    cfg._AVAILABILITY.clear()
    cfg._AVAILABILITY.update(saved)
