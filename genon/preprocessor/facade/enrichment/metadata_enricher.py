from pathlib import Path

import yaml
from docling_core.types import DoclingDocument
from docling.datamodel.pipeline_options import DataEnrichmentOptions
from docling.utils.document_enrichment import enrich_document

from .base_enricher import BaseEnricher

_CONFIG_DIR = Path(__file__).parent.parent / "configs" / "enrich" / "metadata"


class MetadataEnricher(BaseEnricher):
    """문서 메타데이터 추출 enricher.

    config 예시 (yaml 파일 사용):
        {
            "name": "metadata",
            "api_key": "...",
            "config_file": "base",  # configs/enrich/metadata/<name>.yaml
        }

    config 예시 (인라인):
        {
            "name": "metadata",
            "api_key": "...",
            "url": "https://...",
            "model": "mistralai/mistral-small-3.1-24b-instruct",
        }
    """

    def __init__(
        self,
        api_key: str = "",
        config_file: str = "",
        url: str = "",
        model: str = "",
        temperature: float = 0.0,
        top_p: float = 0.00001,
        seed: int = 33,
        max_tokens: int = 10000,
        system_prompt: str = "",
        user_prompt: str = "",
    ):
        cfg = self._load_config(config_file)

        self._options = DataEnrichmentOptions(
            do_toc_enrichment=False,
            extract_metadata=True,
            metadata_api_provider="openrouter",
            metadata_api_base_url=self._resolve_url(url, cfg),
            metadata_api_key=api_key or cfg.get("api_key", ""),
            metadata_model=model or cfg.get("model", ""),
            metadata_temperature=temperature if temperature != 0.0 else cfg.get("temperature", temperature),
            metadata_top_p=top_p if top_p != 0.00001 else cfg.get("top_p", top_p),
            metadata_seed=seed if seed != 33 else cfg.get("seed", seed),
            metadata_max_tokens=max_tokens if max_tokens != 10000 else cfg.get("max_tokens", max_tokens),
            metadata_system_prompt=system_prompt or cfg.get("system_prompt", "").strip(),
            metadata_user_prompt=user_prompt or cfg.get("user_prompt", "").strip(),
        )

    def _load_config(self, config_file: str) -> dict:
        if not config_file:
            return {}
        path = Path(config_file) if Path(config_file).is_absolute() else _CONFIG_DIR / f"{config_file}.yaml"
        if not path.exists():
            raise FileNotFoundError(f"metadata config 없음: {path}")
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    async def enrich(self, document: DoclingDocument, **kwargs) -> DoclingDocument:
        return enrich_document(document, self._options, **kwargs)
