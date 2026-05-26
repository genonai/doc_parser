from pathlib import Path

import yaml
from docling_core.types import DoclingDocument
from docling.datamodel.pipeline_options import DataEnrichmentOptions
from docling.utils.document_enrichment import enrich_document

from .base_enricher import BaseEnricher

_CONFIG_DIR = Path(__file__).parent.parent / "configs" / "enrich" / "toc"


class TOCEnricher(BaseEnricher):
    """TOC 추출 enricher.

    config 예시 (yaml 파일 사용):
        {
            "name": "toc",
            "api_key": "...",
            "config_file": "base",  # configs/enrich/toc/<name>.yaml
        }

    config 예시 (인라인):
        {
            "name": "toc",
            "api_key": "...",
            "model": "mistralai/mistral-small-3.1-24b-instruct",
            "doc_type": "law",
        }
    """

    def __init__(
        self,
        api_key: str = "",
        config_file: str = "",
        genos_url: str = "",
        serving_id: str = "",
        model: str = "",
        doc_type: str = "law",
        extract_metadata: bool = False,
        temperature: float = 0.0,
        top_p: float = 0.00001,
        seed: int = 33,
        max_tokens: int = 10000,
        system_prompt: str = "",
        user_prompt: str = "",
    ):
        cfg = self._load_config(config_file)

        resolved_url = self._resolve_url(cfg, genos_url, serving_id)
        resolved_key = api_key or cfg.get("api_key", "")
        resolved_model = model or cfg.get("model", "")
        resolved_temperature = temperature if temperature != 0.0 else cfg.get("temperature", temperature)
        resolved_top_p = top_p if top_p != 0.00001 else cfg.get("top_p", top_p)
        resolved_seed = seed if seed != 33 else cfg.get("seed", seed)
        resolved_max_tokens = max_tokens if max_tokens != 10000 else cfg.get("max_tokens", max_tokens)

        opts = dict(
            do_toc_enrichment=True,
            toc_doc_type=doc_type,
            extract_metadata=extract_metadata,
            toc_api_provider="openrouter",
            toc_api_base_url=resolved_url,
            toc_api_key=resolved_key,
            toc_model=resolved_model,
            toc_temperature=resolved_temperature,
            toc_top_p=resolved_top_p,
            toc_seed=resolved_seed,
            toc_max_tokens=resolved_max_tokens,
            toc_system_prompt=system_prompt or cfg.get("system_prompt", "").strip(),
            toc_user_prompt=user_prompt or cfg.get("user_prompt", "").strip(),
        )
        if extract_metadata:
            opts.update(
                metadata_api_provider="openrouter",
                metadata_api_base_url=resolved_url,
                metadata_api_key=resolved_key,
                metadata_model=resolved_model,
                metadata_temperature=resolved_temperature,
                metadata_top_p=resolved_top_p,
                metadata_seed=resolved_seed,
                metadata_max_tokens=resolved_max_tokens,
            )
        self._options = DataEnrichmentOptions(**opts)

    def _load_config(self, config_file: str) -> dict:
        if not config_file:
            return {}
        path = Path(config_file) if Path(config_file).is_absolute() else _CONFIG_DIR / f"{config_file}.yaml"
        if not path.exists():
            raise FileNotFoundError(f"toc config 없음: {path}")
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    async def enrich(self, document: DoclingDocument, **kwargs) -> DoclingDocument:
        return enrich_document(document, self._options, **kwargs)
