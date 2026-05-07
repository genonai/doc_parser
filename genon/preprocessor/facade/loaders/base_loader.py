from abc import ABC, abstractmethod
from typing import Any


class BaseLoader(ABC):
    @abstractmethod
    def load(self, file_path: str) -> Any: ...
