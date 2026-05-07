from .base_converter import BaseFormatConverter
from .libreoffice import LibreOfficeConverter
from .image import ImageConverter

CONVERTERS: dict[str, type[BaseFormatConverter]] = {
    "libreoffice": LibreOfficeConverter,
    "image": ImageConverter,
}


def get_converter(name: str) -> BaseFormatConverter:
    cls = CONVERTERS.get(name)
    assert cls is not None, f"알 수 없는 converter: {name}. 가능한 값: {list(CONVERTERS.keys())}"
    return cls()
