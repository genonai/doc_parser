# 구현은 common/logger.py 한 벌이다. 두 진입점(루트 main.py, src/main.py)의 `from logger import Logger` 를 위해 재수출한다.
from common.logger import Logger  # noqa: F401
