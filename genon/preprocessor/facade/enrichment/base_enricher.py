from abc import ABC, abstractmethod
from docling_core.types import DoclingDocument


class BaseEnricher(ABC):
    @abstractmethod
    async def enrich(self, document: DoclingDocument, **kwargs) -> DoclingDocument:
        ...

    @staticmethod
    def _resolve_url(url: str, cfg: dict, genos_url: str = "") -> str:
        if url:
            return url
        if cfg.get("url"):
            return cfg["url"]
        resolved_genos_url = genos_url or cfg.get("genos_url", "")
        serving_id = cfg.get("serving_id", "")
        if resolved_genos_url and serving_id:
            return f"{resolved_genos_url}/api/gateway/rep/serving/{serving_id}/v1/chat/completions"
        return ""
