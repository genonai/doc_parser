from pathlib import Path

import yaml
from docling_core.types import DoclingDocument
from docling.datamodel.pipeline_options import DataEnrichmentOptions
from docling.utils.document_enrichment import enrich_document

from .base_enricher import BaseEnricher

_CONFIG_DIR = Path(__file__).parent.parent / "configs" / "enrich" / "toc"


class TOCEnricher(BaseEnricher):
    """TOC(목차) 추출 enricher.

    메타데이터 추출은 MetadataEnricher를 별도로 사용하세요.

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
        resource_path: str | None = None,
        url: str = "",
        model: str = "",
        doc_type: str = "law",
        temperature: float = 0.0,
        top_p: float = 0.00001,
        seed: int = 33,
        max_tokens: int = 10000,
        system_prompt: str = "",
        user_prompt: str = "",
    ):
        cfg = self._load_config(config_file, resource_path)

        resolved_url = url or cfg.get("url", "")
        resolved_key = api_key or cfg.get("api_key", "")
        resolved_model = model or cfg.get("model", "")
        resolved_temperature = temperature if temperature != 0.0 else cfg.get("temperature", temperature)
        resolved_top_p = top_p if top_p != 0.00001 else cfg.get("top_p", top_p)
        resolved_seed = seed if seed != 33 else cfg.get("seed", seed)
        resolved_max_tokens = max_tokens if max_tokens != 10000 else cfg.get("max_tokens", max_tokens)

        self._options = DataEnrichmentOptions(
            do_toc_enrichment=True,
            toc_doc_type=doc_type,
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
            raise FileNotFoundError(f"toc config 없음: {path}")
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    async def enrich(self, document: DoclingDocument, **kwargs) -> DoclingDocument:
        return enrich_document(document, self._options, **kwargs)
