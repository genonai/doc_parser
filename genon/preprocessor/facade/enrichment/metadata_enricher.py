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
            "name": "extract_metadata",
            "api_key": "...",
            "config_file": "base",  # configs/enrich/metadata/<name>.yaml
        }

    config 예시 (인라인):
        {
            "name": "extract_metadata",
            "api_key": "...",
            "model": "mistralai/mistral-small-3.1-24b-instruct",
        }
    """

    def __init__(
        self,
        api_key: str = "",
        config_file: str = "",
        resource_path: str | None = None,
        url: str = "",
        genos_url: str = "",
        serving_id: str = "",
        model: str = "",
        temperature: float = 0.0,
        top_p: float = 0.00001,
        seed: int = 33,
        max_tokens: int = 10000,
        system_prompt: str = "",
        user_prompt: str = "",
    ):
        cfg = self._load_config(config_file, resource_path)

        resolved_url = self._resolve_url(cfg, genos_url, serving_id, url=url)
        resolved_key = api_key or cfg.get("api_key", "")
        resolved_model = model or cfg.get("model", "")
        resolved_temperature = temperature if temperature != 0.0 else cfg.get("temperature", temperature)
        resolved_top_p = top_p if top_p != 0.00001 else cfg.get("top_p", top_p)
        resolved_seed = seed if seed != 33 else cfg.get("seed", seed)
        resolved_max_tokens = max_tokens if max_tokens != 10000 else cfg.get("max_tokens", max_tokens)

        self._options = DataEnrichmentOptions(
            do_toc_enrichment=False,
            extract_metadata=True,
            metadata_api_provider="openrouter",
            metadata_api_base_url=resolved_url,
            metadata_api_key=resolved_key,
            metadata_model=resolved_model,
            metadata_temperature=resolved_temperature,
            metadata_top_p=resolved_top_p,
            metadata_seed=resolved_seed,
            metadata_max_tokens=resolved_max_tokens,
            metadata_system_prompt=system_prompt or cfg.get("system_prompt", "").strip(),
            metadata_user_prompt=user_prompt or cfg.get("user_prompt", "").strip(),
        )

    def _load_config(self, config_file: str, resource_path: str | None = None) -> dict:
        if not config_file:
            return {}
        cfg_path = Path(config_file)
        if cfg_path.is_absolute():
            path = cfg_path
        elif cfg_path.suffix in {".yaml", ".yml"} or len(cfg_path.parts) > 1:
            if resource_path:
                path = (Path(resource_path) / cfg_path).resolve()
            else:
                path = cfg_path
        else:
            if resource_path:
                candidate = (Path(resource_path) / f"{config_file}.yaml").resolve()
                if candidate.exists():
                    path = candidate
                else:
                    path = _CONFIG_DIR / f"{config_file}.yaml"
            else:
                path = _CONFIG_DIR / f"{config_file}.yaml"
        if not path.exists():
            raise FileNotFoundError(f"metadata config 없음: {path}")
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    async def enrich(self, document: DoclingDocument, **kwargs) -> DoclingDocument:
        return enrich_document(document, self._options, **kwargs)
