import base64
import logging
from pathlib import Path

import httpx
import yaml

_log = logging.getLogger(__name__)
from docling_core.types import DoclingDocument
from docling_core.types.doc import PictureItem
from docling_core.types.doc.document import PictureDescriptionData

from .base_enricher import BaseEnricher

_CONFIG_DIR = Path(__file__).parent.parent / "configs" / "enrich" / "image_description"


class ImageDescriptionEnricher(BaseEnricher):
    """이미지별 LLM 설명 생성 enricher.

    config 예시 (yaml 파일 사용):
        {
            "name": "image_description",
            "api_key": "...",
            "config_file": "base",  # configs/enrich/image_description/<name>.yaml
        }

    config 예시 (인라인):
        {
            "name": "image_description",
            "api_key": "...",
            "model": "gpt-4o",
            "prompt": "이미지를 설명하세요.",
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
        max_tokens: int = 1000,
        temperature: float = 0.1,
        timeout: int = 90,
        prompt: str = "",
    ):
        cfg = self._load_config(config_file, resource_path)
        self._url = self._resolve_url(cfg, genos_url, serving_id, url=url)
        self._model = cfg.get("model", "")
        self._max_tokens = max_tokens if max_tokens != 1000 else cfg.get("max_tokens", max_tokens)
        self._temperature = temperature if temperature != 0.1 else cfg.get("temperature", temperature)
        self._timeout = timeout if timeout != 90 else cfg.get("timeout", timeout)
        self._prompt = prompt or cfg.get("prompt", "").strip()
        self._headers = {"Authorization": f"Bearer {api_key or cfg.get('api_key', '')}", "Content-Type": "application/json"}

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
            raise FileNotFoundError(f"image_description config 없음: {path}")
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    def _get_image_base64(self, item: PictureItem) -> tuple[str, str] | None:
        """(mimetype, base64_data) 반환. 이미지 없으면 None."""
        if item.image is None or item.image.uri is None:
            return None

        uri = str(item.image.uri)
        mimetype = item.image.mimetype or "image/png"

        if uri.startswith("data:"):
            # embedded: data:image/png;base64,<data>
            _, encoded = uri.split(",", 1)
            return mimetype, encoded

        # referenced: 파일 경로
        path = Path(uri)
        if not path.exists():
            return None
        encoded = base64.b64encode(path.read_bytes()).decode()
        return mimetype, encoded

    async def _call_llm(self, mimetype: str, image_b64: str) -> str:
        payload = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": self._prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mimetype};base64,{image_b64}"},
                        },
                    ],
                }
            ],
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(self._url, json=payload, headers=self._headers)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]

    async def enrich(self, document: DoclingDocument, **kwargs) -> DoclingDocument:
        for item, _ in document.iterate_items():
            if not isinstance(item, PictureItem):
                continue
            result = self._get_image_base64(item)
            if result is None:
                continue
            mimetype, image_b64 = result
            try:
                description = await self._call_llm(mimetype, image_b64)
            except Exception as e:
                _log.warning(f"이미지 설명 생성 실패 ({item.self_ref}): {e}")
                continue
            item.annotations.append(
                PictureDescriptionData(kind="description", text=description, provenance=self._model)
            )
        return document
