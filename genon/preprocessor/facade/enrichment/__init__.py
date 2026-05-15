from .base_enricher import BaseEnricher
from .toc_enricher import TOCEnricher
from .image_description_enricher import ImageDescriptionEnricher

ENRICHERS = {
    "toc": TOCEnricher,
    "image_description": ImageDescriptionEnricher,
}

__all__ = ["BaseEnricher", "TOCEnricher", "ImageDescriptionEnricher", "ENRICHERS"]
