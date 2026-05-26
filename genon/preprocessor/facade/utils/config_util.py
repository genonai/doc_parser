import os
from pathlib import Path
from typing import Iterable

import yaml

DEFAULT_CONFIG_ENV_VAR = "DOC_PROCESSOR_CONFIG_PATH"


def resolve_default_config_path(
    filename: str,
    caller_file: str,
    env_var: str = DEFAULT_CONFIG_ENV_VAR,
) -> Path:
    env_path = os.getenv(env_var, "").strip()
    if env_path:
        candidate = Path(env_path).expanduser().resolve()
        if candidate.is_dir():
            return (candidate / filename).resolve()
        return candidate

    base_dir = Path(caller_file).resolve().parent
    local_config = (base_dir / ".." / "resource_dev" / filename).resolve()
    default_config = (base_dir / ".." / "resource" / filename).resolve()

    if local_config.exists():
        return local_config
    return default_config


def load_yaml_config(
    filename: str,
    caller_file: str,
    required_keys: Iterable[str] = (),
    config_label: str = "processor",
    env_var: str = DEFAULT_CONFIG_ENV_VAR,
) -> dict:
    cfg_path = resolve_default_config_path(filename, caller_file, env_var=env_var)
    if not cfg_path.exists():
        raise FileNotFoundError(f"{config_label} config 파일이 없습니다: {cfg_path}")

    loaded = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise TypeError(f"{config_label} config는 dict 형태여야 합니다: {cfg_path}")

    for key in required_keys:
        if key not in loaded:
            raise KeyError(f"{config_label} config에 '{key}'가 없습니다: {cfg_path}")
    return loaded
