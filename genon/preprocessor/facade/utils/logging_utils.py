import logging


_LEVEL_MAP = {
    5: "DEBUG",
    4: "INFO",
    3: "WARNING",
    2: "ERROR",
    1: "CRITICAL",
    0: "NOLOG",
}

_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def setup_logging(level_num: int = 4) -> None:
    """
    5=DEBUG, 4=INFO, 3=WARNING, 2=ERROR, 1=CRITICAL, 0=NOLOG
    """
    level_name = _LEVEL_MAP.get(level_num, "INFO")

    if level_name == "NOLOG":
        logging.disable(logging.CRITICAL)
        return

    logging.disable(logging.NOTSET)

    level = getattr(logging, level_name)
    logging.basicConfig(
        level=level,
        format=_FORMAT,
        handlers=[logging.StreamHandler()],
    )
    logging.getLogger().setLevel(level)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
