from .base_enricher import BaseEnricher
from .toc_enricher import TOCEnricher
from .metadata_enricher import MetadataEnricher
from .image_description_enricher import ImageDescriptionEnricher

ENRICHERS = {
    "toc": TOCEnricher,
    "metadata": MetadataEnricher,
    "image_description": ImageDescriptionEnricher,
}

__all__ = ["BaseEnricher", "TOCEnricher", "MetadataEnricher", "ImageDescriptionEnricher", "ENRICHERS"]
