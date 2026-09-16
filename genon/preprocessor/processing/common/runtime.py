"""프로세스 런타임 설정.

facade 5종이 각자 복제해 두었던 로깅 레벨 설정의 단일 사본이다. docling 에
의존하지 않는다.
"""

from __future__ import annotations

import logging

_LEVEL_NAMES = {5: "DEBUG", 4: "INFO", 3: "WARNING", 2: "ERROR", 1: "CRITICAL", 0: "NOLOG"}


def setup_logging(level_num: int, announce=print) -> None:
    """5"DEBUG", 4"INFO", 3"WARNING", 2"ERROR", 1"CRITICAL", 0"NOLOG" 중 하나를 받아
    로깅 레벨을 설정한다.

    announce 는 선택한 레벨을 알리는 출력 함수다. 대부분의 facade 는 basicConfig
    이전이라도 보이도록 print 를 쓰고, 첨부 프로세서만 로거를 쓴다 — 기존 동작을
    그대로 유지하려고 인자로 뺐다.
    """
    level_name = _LEVEL_NAMES.get(level_num, "INFO")
    announce(f"Setting log level to: {level_name}")

    if level_name == "NOLOG" or not hasattr(logging, level_name):
        logging.disable(logging.CRITICAL)  # 모든 로그 비활성화
        return

    level = getattr(logging, level_name.upper())

    # root logger 설정 (핸들러는 main에서만 설정)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler()],  # 콘솔 출력
    )
    logging.getLogger().setLevel(level)
