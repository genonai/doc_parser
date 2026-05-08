from .base_loader import BaseLoader
from .docling_loader import DoclingLoader
from .tabular_loader import TabularLoader
from .audio_loader import AudioLoader
from .genos_hwp_loader import GenosHwpLoader

LOADERS: dict[str, type[BaseLoader]] = {
    "docling": DoclingLoader,
    "tabular": TabularLoader,
    "audio": AudioLoader,
    "genos_hwp": GenosHwpLoader,
}


def get_loader(name: str, **kwargs) -> BaseLoader:
    cls = LOADERS.get(name)
    assert cls is not None, f"알 수 없는 loader: {name}. 가능한 값: {list(LOADERS.keys())}"
    return cls(**kwargs)
