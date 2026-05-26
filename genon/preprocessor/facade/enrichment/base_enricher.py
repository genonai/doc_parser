from abc import ABC, abstractmethod
from docling_core.types import DoclingDocument


class BaseEnricher(ABC):
    @abstractmethod
    async def enrich(self, document: DoclingDocument, **kwargs) -> DoclingDocument:
        ...

    @staticmethod
    def _resolve_url(cfg: dict, genos_url: str = "", serving_id: str = "") -> str:
        resolved_genos_url = genos_url or cfg.get("genos_url", "")
        resolved_serving_id = serving_id or cfg.get("serving_id", "")
        if resolved_genos_url and resolved_serving_id:
            return f"{resolved_genos_url}/api/gateway/rep/serving/{resolved_serving_id}/v1/chat/completions"
        return ""
